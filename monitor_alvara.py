import os
import time
import requests
from bs4 import BeautifulSoup
from google import genai
from google.api_core.exceptions import ResourceExhausted

# ==========================================
# CONFIGURAÇÕES
# ==========================================
ID_INICIAL = 49478
ID_FINAL = 49480
ARQUIVO_CONTROLE = "projetos_enviados.txt"

# Limites da API Gemini free tier (gemini-2.0-flash)
# 15 RPM / 1 million TPM / 1500 RPD
DELAY_ENTRE_CHAMADAS_IA = 5        # segundos entre chamadas ao Gemini
MAX_RETRIES_GEMINI = 3              # tentativas por projeto em caso de 429
MAX_ERROS_SITE_CONSECUTIVOS = 10    # erros do site antes de parar
MAX_PROJETOS_POR_EXECUCAO = 250     # limite por execução (~5 min de GitHub Actions)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

if GOOGLE_API_KEY:
    client = genai.Client(api_key=GOOGLE_API_KEY)
else:
    print("ERRO: GOOGLE_API_KEY não configurada.")
    exit(1)

# PROMPT REFINADO: Menos restritivo para evitar o erro do "VAZIO" em projetos reais
PROMPT_BASE = """
Analise os dados extraídos do sistema Alvará Fácil da Prefeitura de Goiânia abaixo.
Sua tarefa é extrair os detalhes do empreendimento para um relatório imobiliário.

DADOS DESEJADOS:
- Número do projeto e situação atual.
- Dados do proprietário e localização (Setor, Endereço, IPTU).
- Características Técnicas: Área do terreno, pavimentos, unidades, analista.
- Histórico: Datas de abertura e despachos mais relevantes.

REGRAS DE RESPOSTA:
1. Se encontrar QUALQUER dado útil (mesmo que incompleto), elabore o resumo com o que houver.
2. Use emojis e negrito (*texto*) para leitura fácil no WhatsApp/Telegram.
3. Se a página estiver TOTALMENTE sem dados de projeto ou for apenas uma tela de erro do sistema, responda APENAS: VAZIO
"""


def carregar_enviados():
    if not os.path.exists(ARQUIVO_CONTROLE):
        return set()
    with open(ARQUIVO_CONTROLE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())


def salvar_enviado(projeto_id):
    with open(ARQUIVO_CONTROLE, 'a', encoding='utf-8') as f:
        f.write(f"{projeto_id}\n")


def send_telegram_message(text):
    if not text:
        return
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Quebra de mensagens longas (limite Telegram ~4096 chars)
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "Markdown"}
        try:
            requests.post(url_api, json=payload, timeout=30)
        except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")


def chamar_gemini(prompt_final):
    """Chama a API Gemini com retry e backoff exponencial para erros 429."""
    for tentativa in range(1, MAX_RETRIES_GEMINI + 1):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt_final
            )
            return response.text.strip()
        except ResourceExhausted as e:
            wait_time = DELAY_ENTRE_CHAMADAS_IA * (2 ** tentativa)  # 10s, 20s, 40s
            print(f"   ⏳ Gemini 429 (tentativa {tentativa}/{MAX_RETRIES_GEMINI}). Aguardando {wait_time}s...")
            time.sleep(wait_time)
        except Exception as e:
            print(f"   ❌ Erro inesperado no Gemini: {e}")
            return None
    print(f"   ❌ Gemini: todas as {MAX_RETRIES_GEMINI} tentativas falharam (quota esgotada).")
    return None


def main():
    enviados = carregar_enviados()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://www10.goiania.go.gov.br/alvarafacil/',
        'Connection': 'keep-alive'
    }

    erros_site_consecutivos = 0
    projetos_processados = 0
    projetos_enviados = 0

    print(f"Iniciando varredura do ID {ID_INICIAL} ao {ID_FINAL} (max {MAX_PROJETOS_POR_EXECUCAO} por execução)...")

    for projeto_id in range(ID_INICIAL, ID_FINAL + 1):
        # Limite de projetos por execução para caber no timeout do GitHub Actions
        if projetos_processados >= MAX_PROJETOS_POR_EXECUCAO:
            print(f"🔄 Limite de {MAX_PROJETOS_POR_EXECUCAO} projetos atingido. Continuará na próxima execução.")
            break

        if str(projeto_id) in enviados:
            continue

        url = f"https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx?ProjetoId={projeto_id}&TipoAlvara=2"
        print(f"Lendo Projeto {projeto_id}...")

        try:
            res = requests.get(url, headers=headers, timeout=20)
            res.raise_for_status()

            soup = BeautifulSoup(res.text, 'html.parser')
            conteudo = soup.find(id="GoianiaTheme_wt8_block_wtMainContent")
            if not conteudo:
                conteudo = soup

            # Verifica "Erro Interno" do site da prefeitura
            elemento_titulo = soup.find(id="GoianiaTheme_wt8_block_wtTitle")
            if elemento_titulo and "Erro Interno" in elemento_titulo.get_text():
                erros_site_consecutivos += 1
                print(f"⚠️ Erro Interno no ID {projeto_id} (Consecutivos: {erros_site_consecutivos})")
                if erros_site_consecutivos >= MAX_ERROS_SITE_CONSECUTIVOS:
                    send_telegram_message(
                        f"🛑 *Bot pausado:* {MAX_ERROS_SITE_CONSECUTIVOS} erros seguidos no site da Prefeitura.\n"
                        f"Último ID tentado: {projeto_id}\n"
                        f"Projetos enviados nesta execução: {projetos_enviados}\n"
                        f"Tentará novamente na próxima execução agendada."
                    )
                    break
                time.sleep(2)
                continue

            # Reset do contador de erros do site quando uma página carrega OK
            erros_site_consecutivos = 0
            projetos_processados += 1

            texto_limpo = conteudo.get_text(separator='\n', strip=True)
            if len(texto_limpo) < 200:
                print(f"Projeto {projeto_id} parece estar vazio ou não carregou.")
                continue

            prompt_final = f"{PROMPT_BASE}\n--- TEXTO DA PÁGINA ---\n{texto_limpo}\n\nLink: {url}"

            # Chamada da IA com retry
            resposta_ia = chamar_gemini(prompt_final)

            if resposta_ia is None:
                print(f"⏭️ Projeto {projeto_id} pulado (Gemini indisponível).")
                continue

            if "VAZIO" in resposta_ia.upper() and len(resposta_ia) < 15:
                print(f"Projeto {projeto_id} ignorado pela IA (sem dados relevantes).")
            else:
                send_telegram_message(resposta_ia)
                salvar_enviado(projeto_id)
                projetos_enviados += 1
                print(f"✅ Projeto {projeto_id} enviado com sucesso!")

            # Delay entre chamadas para respeitar rate limit do Gemini free tier
            time.sleep(DELAY_ENTRE_CHAMADAS_IA)

        except requests.exceptions.ConnectionError as e:
            erros_site_consecutivos += 1
            print(f"❌ Erro de conexão no projeto {projeto_id}: {e}")
            if erros_site_consecutivos >= MAX_ERROS_SITE_CONSECUTIVOS:
                send_telegram_message(
                    f"🛑 *Bot pausado:* Site da Prefeitura fora do ar.\n"
                    f"Último ID tentado: {projeto_id}\n"
                    f"Tentará novamente na próxima execução."
                )
                break
            time.sleep(5)

        except Exception as e:
            print(f"❌ Erro no projeto {projeto_id}: {e}")
            time.sleep(3)

    # Resumo final
    resumo = (
        f"📊 *Varredura concluída!*\n"
        f"Projetos analisados: {projetos_processados}\n"
        f"Projetos enviados: {projetos_enviados}"
    )
    print(resumo)
    if projetos_enviados > 0:
        send_telegram_message(resumo)

    # Commit do arquivo de controle
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system(f'git add {ARQUIVO_CONTROLE}')
    os.system('git diff --staged --quiet || (git commit -m "bot: atualiza projetos processados" && git pull --rebase && git push)')


if __name__ == "__main__":
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and GOOGLE_API_KEY:
        main()
    else:
        print("Erro: Variáveis de ambiente faltando.")
