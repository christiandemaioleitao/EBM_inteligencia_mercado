import requests
from lxml import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import json

# ==============================================================================
#                          ÁREA DE CONTROLE (CONFIGURAÇÕES)
# ==============================================================================
PROJETO_INICIO = 43532
PROJETO_FIM = 43535
MAX_WORKERS = 15
TIMEOUT_REQUEST = 25
BASE_URL = "https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx"
TIPO_ALVARA = 2

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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
- Data do protocolo do projeto (primeira data) e a data da ultima movimentação e quero que você calcule o tempo decorrido em dias (caso o tempo seja inferior a 31 dias) e em meses (caso o tempo seja igual ou superior a 31 dias)
- Qualquer observação relevante

Dados do projeto:
{dados_json}

Responda APENAS com o texto do resumo, sem títulos ou formatação extra."""

# ==============================================================================
#                                FIM DA CONFIGURAÇÃO
# ==============================================================================

def escape_tg_html(text):
    """Escapa caracteres que quebram o parse_mode='HTML' rigoroso do Telegram."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def enviar_mensagens_telegram(mensagens):
    """Envia uma lista de mensagens individualmente para contornar limites de tamanho e formatação."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram não configurado. Pulando envio.")
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
        
        # Pausa de 2 segundos para respeitar o Rate Limit do Telegram (evitar bloqueio)
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
        dados['Data Pagamento'] = get_label_text("Data Pagamento Taxa Inicial")
        dados['Autor'] = get_label_text("Autor")

        raw_text = " ".join(tree.xpath('//*[contains(@id, "Identificacao")]//text()'))
        emails = re.findall(r'[\w\.-]+@[\w\.-]+', raw_text)
        dados['Email Autor'] = emails[0] if emails else "Não Informado"
        dados['Telefones'] = get_label_text("Telefones")
        dados['Proprietário'] = get_input_val('wtPessoa_NomePessoa')
        dados['CPF/CNPJ'] = get_input_val('wtPessoa_NumeroCpfCnpj')
        dados['Email Prop.'] = get_input_val('wtPessoa_Email')
        dados['Endereço'] = get_input_val('wtProjeto_ComplementoEndereco2')
        dados['Complemento'] = get_input_val('wtProjeto_ComplementoEndereco')
        dados['IPTU'] = get_input_val('wtNumeroCadImobiliario')
        dados['Área Terreno'] = get_input_val('wtProjeto_AreaTotal')
        pavimentos_val = tree.xpath("//label[contains(text(), 'Nr de Pavimentos')]/following-sibling::input/@value")
        dados['Nº Pavimentos'] = pavimentos_val[0] if pavimentos_val else "Não Informado"
        dados['Desc. Pavimentos'] = get_input_val('wtProjeto_DescricaoPavimentos')
        dados['Incorporador'] = get_input_val('wtProjeto_Incorporadora')
        dados['Unidades'] = get_input_val('wtQuantidadeUnidades2')
        dados['Área Existente'] = get_input_val('wtProjeto_Areaexistente')
        dados['Área Acréscimo'] = get_input_val('wtProjeto_Area')

        return dados
    except Exception as e:
        return {'ID Projeto': projeto_id, 'Status': f'Erro: {str(e)}'}

def gerar_resumo_groq(dados_projeto):
    projeto_id = dados_projeto.get('ID Projeto', '?')

    if dados_projeto.get('Status') != 'Sucesso':
        return projeto_id, f"[Projeto {projeto_id}] Ignorado — {dados_projeto.get('Status', 'Erro desconhecido')}"

    dados_envio = {k: v for k, v in dados_projeto.items() if k != 'Status'}
    prompt = PROMPT_RESUMO.format(dados_json=json.dumps(dados_envio, ensure_ascii=False, indent=2))

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 512
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            texto = resp.json()['choices'][0]['message']['content']
            return projeto_id, texto.strip()
        elif resp.status_code == 429:
            time.sleep(3)
            resp2 = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
            if resp2.status_code == 200:
                texto = resp2.json()['choices'][0]['message']['content']
                return projeto_id, texto.strip()
            return projeto_id, f"[Projeto {projeto_id}] Erro Groq (rate limit): {resp2.status_code}"
        else:
            return projeto_id, f"[Projeto {projeto_id}] Erro Groq: {resp.status_code} — {resp.text[:200]}"
    except Exception as e:
        return projeto_id, f"[Projeto {projeto_id}] Erro Groq: {str(e)}"

def executar_varredura(inicio, fim):
    lista_ids = range(inicio, fim + 1)
    total = len(lista_ids)
    resultados_brutos = []

    with requests.Session() as session:
        print(f"\n---> ETAPA 1/2: Extraindo dados ({total} projetos, ID {inicio} ao {fim})")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(extrair_dados_projeto, pid, session): pid for pid in lista_ids}
            for i, future in enumerate(as_completed(futures)):
                resultados_brutos.append(future.result())
                progresso = i + 1
                if progresso % 5 == 0 or progresso == total:
                    print(f"  [{(progresso/total)*100:.1f}%] Extraído: {progresso}/{total}")
        print(f"---> Extração: {time.time()-start_time:.2f}s")

    resultados_brutos.sort(key=lambda x: x.get('ID Projeto', 0))

    # ==============================================================================
    #                      VERIFICAÇÃO DE REGRA (CORTAR A FILA)
    # ==============================================================================
    projetos_validados = []
    falhas_seguidas = 0
    
    for dados in resultados_brutos:
        tipo = dados.get('Tipo', 'Não Informado')
        numero = dados.get('Número', 'Não Informado')

        if tipo == 'Não Informado' or numero == 'Não Informado':
            falhas_seguidas += 1
            dados['Ignorar'] = True
            projetos_validados.append(dados)
            
            if falhas_seguidas >= 3:
                print(f"\n🛑 ALERTA: 3 projetos seguidos com Tipo ou Nº 'Não Informado'.")
                print("🛑 Cortando a fila de processamento. Os projetos válidos anteriores serão enviados.")
                break  # Sai do loop e ignora o restante dos IDs extraídos
        else:
            falhas_seguidas = 0
            dados['Ignorar'] = False
            projetos_validados.append(dados)
            
    # Atualiza a lista para conter apenas os projetos até o momento do corte
    resultados_brutos = projetos_validados
    # ==============================================================================

    print(f"\n---> ETAPA 2/2: Resumos via Groq ({GROQ_MODEL})...")
    resumos = {}
    start_groq = time.time()
    groq_workers = min(5, MAX_WORKERS)
    
    # Filtra apenas os válidos para não gastar token do Groq atoa
    projetos_validos = [d for d in resultados_brutos if not d.get('Ignorar')]

    if not projetos_validos:
        print("  ⚠️ Nenhum projeto válido para ser resumido.")
    else:
        with ThreadPoolExecutor(max_workers=groq_workers) as executor:
            futures = {executor.submit(gerar_resumo_groq, d): d['ID Projeto'] for d in projetos_validos}
            for i, future in enumerate(as_completed(futures)):
                pid, texto = future.result()
                resumos[pid] = texto
                progresso = i + 1
                if progresso % 5 == 0 or progresso == len(projetos_validos):
                    print(f"  [{(progresso/len(projetos_validos))*100:.1f}%] Resumo: {progresso}/{len(projetos_validos)}")
    
    print(f"---> Resumos: {time.time()-start_groq:.2f}s")

    return resultados_brutos, resumos

def montar_mensagens_telegram(dados_brutos, resumos, inicio, fim):
    """Monta mensagens separadas para evitar quebra de HTML e limite de caracteres."""
    agora = time.strftime('%d/%m/%Y %H:%M')
    mensagens = []
    
    cabecalho = f"<b>📊 ALVARÁS GOIÂNIA — Processamento de IDs</b>\n<i>{agora}</i>"
    mensagens.append(cabecalho)

    for dados in dados_brutos:
        # Pula a montagem da mensagem se a flag "Ignorar" for True
        if dados.get('Ignorar'):
            continue

        pid = dados['ID Projeto']
        linhas = [f"<b>📋 Projeto {pid}</b>"]
        
        if dados.get('Status') == 'Sucesso':
            linhas.append(f"  Nº: {escape_tg_html(dados.get('Número', 'N/I'))}")
            linhas.append(f"  Tipo: {escape_tg_html(dados.get('Tipo', 'N/I'))}")
            linhas.append(f"  Situação: {escape_tg_html(dados.get('Situação', 'N/I'))}")
            linhas.append(f"  Endereço: {escape_tg_html(dados.get('Endereço', 'N/I'))}")
            linhas.append(f"  Proprietário: {escape_tg_html(dados.get('Proprietário', 'N/I'))}")
            linhas.append("")
            linhas.append(f"<b>🤖 Resumo:</b>")
            resumo = escape_tg_html(resumos.get(pid, "Não disponível"))
            linhas.append(resumo)
        else:
            linhas.append(f"  ⚠️ {escape_tg_html(dados.get('Status', 'Erro'))}")
            
        mensagens.append("\n".join(linhas))

    return mensagens

def montar_texto_console(dados_brutos, resumos, inicio, fim):
    linhas = [
        "=" * 60,
        f"  RESUMOS DE PROJETOS",
        f"  Gerado em: {time.strftime('%d/%m/%Y %H:%M:%S')}",
        "=" * 60
    ]

    for dados in dados_brutos:
        pid = dados['ID Projeto']
        linhas.append(f"\n{'─' * 60}")
        linhas.append(f"📋 PROJETO ID {pid}")
        linhas.append(f"{'─' * 60}")

        # Avisa no console que o projeto foi ignorado
        if dados.get('Ignorar'):
            linhas.append(f"  ⚠️ Projeto Ignorado (Tipo ou Nº Não Informado)")
            continue

        if dados.get('Status') == 'Sucesso':
            linhas.append(f"  Número:       {dados.get('Número', 'N/I')}")
            linhas.append(f"  Tipo:         {dados.get('Tipo', 'N/I')}")
            linhas.append(f"  Situação:     {dados.get('Situação', 'N/I')}")
            linhas.append(f"  Endereço:     {dados.get('Endereço', 'N/I')}")
            linhas.append(f"  Proprietário: {dados.get('Proprietário', 'N/I')}")
            linhas.append("")
            linhas.append("  🤖 Resumo Groq:")
            resumo = resumos.get(pid, "Não disponível")
            for lr in resumo.split('\n'):
                linhas.append(f"     {lr}")
        else:
            linhas.append(f"  ⚠️ {dados.get('Status', 'Erro')}")

    return "\n".join(linhas)

# --- EXECUÇÃO PRINCIPAL ---
if __name__ == "__main__":
    print("=" * 60)
    print("  EXTRATOR DE ALVARÁS + GROQ + TELEGRAM")
    print("=" * 60)

    erros = []
    if not GROQ_API_KEY:
        erros.append("GROQ_API_KEY não configurada")
    if not TELEGRAM_TOKEN:
        erros.append("TELEGRAM_TOKEN não configurado")
    if not TELEGRAM_CHAT_ID:
        erros.append("TELEGRAM_CHAT_ID não configurado")

    if erros:
        print("\n⚠️  Variáveis faltando:")
        for e in erros:
            print(f"   - {e}")
        if not GROQ_API_KEY:
            print("\n❌ Sem GROQ_API_KEY não é possível continuar. Abortando.")
            exit(1)
        print("\n⚠️  Continuando sem Telegram...\n")

    print(f"\n📌 Intervalo: Projeto {PROJETO_INICIO} ao {PROJETO_FIM}")

    dados_brutos, resumos = executar_varredura(PROJETO_INICIO, PROJETO_FIM)

    texto_console = montar_texto_console(dados_brutos, resumos, PROJETO_INICIO, PROJETO_FIM)
    print("\n" + texto_console)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        print("\n---> Enviando para o Telegram...")
        mensagens_tg = montar_mensagens_telegram(dados_brutos, resumos, PROJETO_INICIO, PROJETO_FIM)
        
        # Só envia se houver mais que apenas o cabeçalho
        if len(mensagens_tg) > 1:
            enviar_mensagens_telegram(mensagens_tg)
        else:
            print("  ⚠️ Nenhuma mensagem válida para enviar.")
    else:
        print("\n⚠️  Envio ao Telegram pulado (credenciais não configuradas).")

    print("\n✅ Execução finalizada.")
