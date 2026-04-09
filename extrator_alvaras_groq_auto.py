import requests
from lxml import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import json

# ==============================================================================
#                         ÁREA DE CONTROLE (CONFIGURAÇÕES)
# ==============================================================================
PROJETO_INICIO = 49481
PROJETO_FIM = 49650
MAX_WORKERS = 10 # Reduzido levemente para evitar bloqueios em CI/CD
TIMEOUT_REQUEST = 30
BASE_URL = "https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx"
TIPO_ALVARA = 2

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

PROMPT_RESUMO = """Você é um analista de projetos de construção civil. Com base nos dados extraídos abaixo de um projeto de alvará de construção da Prefeitura de Goiânia, gere um resumo objetivo e profissional em português, com no máximo 6 linhas.

O resumo deve conter:
- Tipo e situação do projeto
- Localização e área do terreno
- Porte da obra (pavimentos, unidades, áreas)
- Responsáveis (autor, proprietário, incorporador se houver)
- Data do protocolo do projeto (primeira data) e a data da última movimentação. Calcule o tempo decorrido em dias (se < 31 dias) ou meses (se >= 31 dias).
- Qualquer observação relevante.

Dados do projeto:
{dados_json}

Responda APENAS com o texto do resumo, sem títulos ou formatação extra."""

ARQUIVO_IDS_ENVIADOS = "ids_enviados.json"

# ==============================================================================
#                               FIM DA CONFIGURAÇÃO
# ==============================================================================

def carregar_ids_enviados():
    if os.path.exists(ARQUIVO_IDS_ENVIADOS):
        try:
            with open(ARQUIVO_IDS_ENVIADOS, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def salvar_ids_enviados(ids_set):
    try:
        with open(ARQUIVO_IDS_ENVIADOS, 'w', encoding='utf-8') as f:
            json.dump(sorted(list(ids_set)), f)
    except Exception as e:
        print(f"⚠️ Erro ao salvar IDs: {e}")

def escape_tg_html(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def enviar_mensagens_telegram(mensagens):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram não configurado. Pulando envio.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    for i, msg in enumerate(mensagens):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                print(f"  ✅ Telegram: Mensagem {i+1}/{len(mensagens)} enviada")
            else:
                print(f"  ❌ Telegram erro {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  ❌ Telegram erro na conexão: {e}")
        
        time.sleep(2)
    return True

def extrair_dados_projeto(projeto_id, session):
    url = f'{BASE_URL}?ProjetoId={projeto_id}&TipoAlvara={TIPO_ALVARA}'
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT_REQUEST)
        if response.status_code != 200:
            return {'ID Projeto': projeto_id, 'Status': f'Erro HTTP {response.status_code}'}

        tree = html.fromstring(response.content)
        dados = {'ID Projeto': projeto_id, 'Status': 'Sucesso'}

        def get_input_val(suffix_id):
            vals = tree.xpath(f"//input[contains(@id, '{suffix_id}')]/@value")
            return vals[0].strip() if vals else "Não Informado"

        def get_label_text(label_text):
            res = tree.xpath(f"//label[normalize-space()='{label_text}']/../following-sibling::div//span/text()")
            if not res:
                res = tree.xpath(f"//label[normalize-space()='{label_text}']/../text()")
            return res[0].strip() if res else "Não Informado"

        dados['Número'] = get_label_text("Número")
        dados['Tipo'] = get_label_text("Tipo")
        dados['Situação'] = get_label_text("Situação")
        dados['Autor'] = get_label_text("Autor")
        
        raw_text = " ".join(tree.xpath('//*[contains(@id, "Identificacao")]//text()'))
        emails = re.findall(r'[\w\.-]+@[\w\.-]+', raw_text)
        dados['Email Autor'] = emails[0] if emails else "Não Informado"
        
        dados['Proprietário'] = get_input_val('wtPessoa_NomePessoa')
        dados['Endereço'] = get_input_val('wtProjeto_ComplementoEndereco2')
        dados['Área Terreno'] = get_input_val('wtProjeto_AreaTotal')
        
        pav_xpath = "//label[contains(text(), 'Nr de Pavimentos')]/following-sibling::input/@value"
        pavimentos_val = tree.xpath(pav_xpath)
        dados['Nº Pavimentos'] = pavimentos_val[0] if pavimentos_val else "Não Informado"
        
        return dados
    except Exception as e:
        return {'ID Projeto': projeto_id, 'Status': f'Erro: {str(e)}'}

def gerar_resumo_groq(dados_projeto):
    projeto_id = dados_projeto.get('ID Projeto', '?')
    if dados_projeto.get('Status') != 'Sucesso':
        return projeto_id, f"[Projeto {projeto_id}] Erro na extração."

    prompt = PROMPT_RESUMO.format(dados_json=json.dumps(dados_projeto, ensure_ascii=False, indent=2))
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            return projeto_id, resp.json()['choices'][0]['message']['content'].strip()
        return projeto_id, f"Erro Groq: {resp.status_code}"
    except Exception as e:
        return projeto_id, f"Erro Conexão Groq: {str(e)}"

def executar_varredura(inicio, fim):
    ids_ja_enviados = carregar_ids_enviados()
    lista_ids = [pid for pid in range(inicio, fim + 1) if pid not in ids_ja_enviados]

    if not lista_ids:
        print("\n✅ Nada novo para processar.")
        return [], {}

    resultados_brutos = []
    with requests.Session() as session:
        print(f"\n---> Extraindo {len(lista_ids)} projetos...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(extrair_dados_projeto, pid, session): pid for pid in lista_ids}
            for i, future in enumerate(as_completed(futures)):
                resultados_brutos.append(future.result())

    resultados_brutos.sort(key=lambda x: x.get('ID Projeto', 0))

    # Lógica de corte (Stop early if sequential fails)
    projetos_validados = []
    falhas_seguidas = 0
    for dados in resultados_brutos:
        if dados.get('Número') == 'Não Informado' or 'Erro' in dados.get('Status'):
            falhas_seguidas += 1
            dados['Ignorar'] = True
        else:
            falhas_seguidas = 0
            dados['Ignorar'] = False
        
        projetos_validados.append(dados)
        if falhas_seguidas >= 3:
            print("🛑 Muitas falhas seguidas. Encerrando busca.")
            break

    print("\n---> Gerando resumos via Groq...")
    resumos = {}
    projetos_para_resumir = [d for d in projetos_validados if not d.get('Ignorar')]
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(gerar_resumo_groq, d): d['ID Projeto'] for d in projetos_para_resumir}
        for future in as_completed(futures):
            pid, texto = future.result()
            resumos[pid] = texto

    return projetos_validados, resumos

def montar_mensagens_telegram(dados_brutos, resumos):
    mensagens = [f"<b>📊 ALVARÁS GOIÂNIA</b>\n<i>{time.strftime('%d/%m/%Y %H:%M')}</i>"]
    
    for d in dados_brutos:
        if d.get('Ignorar'): continue
        pid = d['ID Projeto']
        msg = (f"<b>📋 Projeto {pid}</b>\n"
               f"Nº: {escape_tg_html(d.get('Número'))}\n"
               f"Tipo: {escape_tg_html(d.get('Tipo'))}\n"
               f"<b>🤖 Resumo:</b>\n{escape_tg_html(resumos.get(pid, 'N/A'))}")
        mensagens.append(msg)
    return mensagens

if __name__ == "__main__":
    if not GROQ_API_KEY:
        print("❌ Erro: GROQ_API_KEY não encontrada.")
        exit(1)

    print(f"🚀 Iniciando varredura: {PROJETO_INICIO} a {PROJETO_FIM}")
    dados, resumos = executar_varredura(PROJETO_INICIO, PROJETO_FIM)

    if any(not d.get('Ignorar') for d in dados):
        msgs = montar_mensagens_telegram(dados, resumos)
        if enviar_mensagens_telegram(msgs):
            enviados = {d['ID Projeto'] for d in dados if not d.get('Ignorar')}
            historico = carregar_ids_enviados()
            historico.update(enviados)
            salvar_ids_enviados(historico)
    else:
        print("Nenhum projeto novo válido encontrado.")
