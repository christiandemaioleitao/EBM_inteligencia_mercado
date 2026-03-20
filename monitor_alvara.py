import os
import time
import requests
from bs4 import BeautifulSoup
from google import genai

# ==========================================
# CONFIGURAÇÕES
# ==========================================
ID_INICIAL = 49250
ID_FINAL = 49270
ARQUIVO_CONTROLE = "projetos_enviados.txt"

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
        if not text: return
                url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Quebra de mensagens longas (limite Telegram ~4096 chars)
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
                payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "Markdown"}
                try:
                                requests.post(url_api, json=payload, timeout=30)
except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")

def main():
        enviados = carregar_enviados()

    # HEADERS AVANÇADOS: Simula um navegador real para evitar bloqueios e garantir o render inicial
    headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://www10.goiania.go.gov.br/alvarafacil/',
                'Connection': 'keep-alive'
    }

    erros_consecutivos = 0
    print(f"Iniciando varredura do ID {ID_INICIAL} ao {ID_FINAL}...")

    for projeto_id in range(ID_INICIAL, ID_FINAL + 1):
                if str(projeto_id) in enviados:
                                print(f"Projeto {projeto_id} já enviado. Pulando...")
                                continue

                url = f"https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx?ProjetoId={projeto_id}&TipoAlvara=2"
                print(f"Lendo Projeto {projeto_id}...")

        try:
                        res = requests.get(url, headers=headers, timeout=20)
                        res.raise_for_status() # Garante que a requisição foi 200 OK

            soup = BeautifulSoup(res.text, 'html.parser')

            # --- TRIAGEM DE CONTEÚDO ---
            # Tenta focar no container principal do sistema para ignorar menus e rodapés
            conteudo = soup.find(id="GoianiaTheme_wt8_block_wtMainContent")
            if not conteudo:
                                conteudo = soup # Se não achar o ID, pega a página toda como fallback

            # Checagem de erro interno no título da prefeitura
            elemento_titulo = soup.find(id="GoianiaTheme_wt8_block_wtTitle")
            if elemento_titulo and "Erro Interno" in elemento_titulo.get_text():
                                erros_consecutivos += 1
                                print(f"⚠️ Erro Interno no ID {projeto_id} (Consecutivos: {erros_consecutivos})")
                                if erros_consecutivos >= 5:
                                                        send_telegram_message("🛑 Bot interrompido: Muitos erros seguidos no site da Prefeitura.")
                                                        break
                                                    continue

            erros_consecutivos = 0
            texto_limpo = conteudo.get_text(separator='\n', strip=True)

            # Validação básica de tamanho (se vier quase nada de texto, a página não carregou)
            if len(texto_limpo) < 200:
                                print(f"Projeto {projeto_id} parece estar vazio ou não carregou.")
                continue

            prompt_final = f"{PROMPT_BASE}\n--- TEXTO DA PÁGINA ---\n{texto_limpo}\n\nLink: {url}"

            # Chamada da IA (usando o novo SDK google-genai)
            response = client.models.generate_content(
                                model='gemini-2.0-flash',
                                contents=prompt_final
            )
            resposta_ia = response.text.strip()

            # Lógica de verificação: Se a IA respondeu VAZIO e o texto for curto, ignoramos.
            if "VAZIO" in resposta_ia.upper() and len(resposta_ia) < 15:
                                print(f"Projeto {projeto_id} ignorado pela IA (sem dados relevantes).")
else:
                send_telegram_message(resposta_ia)
                salvar_enviado(projeto_id)
                print(f"✅ Projeto {projeto_id} enviado com sucesso!")

except Exception as e:
            print(f"❌ Erro crítico no projeto {projeto_id}: {e}")

        # Delay amigável para não ser bloqueado (WAF)
        time.sleep(5) 

    print("Varredura concluída!")

    # Automação do Git para o GitHub Actions
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system(f'git add {ARQUIVO_CONTROLE}')
    os.system('git diff --staged --quiet || (git commit -m "bot: atualiza projetos processados" && git pull --rebase && git push)')

if __name__ == "__main__":
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and GOOGLE_API_KEY:
                    main()
else:
        print("Erro: Variáveis de ambiente faltando.")
