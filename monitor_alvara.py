import os
import time
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

# ==========================================
# CONFIGURAÇÕES
# ==========================================
ID_INICIAL = 47000
ID_FINAL = 47100
ARQUIVO_CONTROLE = "projetos_enviados.txt"

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    print("ERRO: GOOGLE_API_KEY não configurada.")
    exit(1)

PROMPT_BASE = """
Analise a página do sistema Alvará Fácil da Prefeitura de Goiânia fornecida abaixo e elabore um resumo completo do empreendimento formatado para envio pelo WhatsApp, incluindo:

- Número do projeto e situação atual
- Licença prévia e data de pagamento da taxa inicial
- Dados do proprietário (nome, CNPJ/CPF, tipo pessoa)
- Localização completa (endereço, setor, IPTU, área do terreno)
- Dados do projeto (tipo de uso, HIS, quantidade de unidades, número de pavimentos, descrição dos pavimentos, área a ser construída, analista responsável)
- Vagas atendidas (comercial e habitação/visitante, incluindo PCD)
- Responsáveis técnicos (nome e CAE)
- Histórico resumido dos andamentos com as datas mais relevantes (abertura, análises, resultado final)
- Status dos anexos relevantes

Use formatação com emojis e negrito (*texto*) compatível com WhatsApp.
Coloque o link da página ao final do texto.

REGRA MUITO IMPORTANTE: Se o texto abaixo não contiver as informações mínimas de um alvará, responda APENAS com a palavra: VAZIO

---
TEXTO DA PÁGINA:
"""

def carregar_enviados():
    if not os.path.exists(ARQUIVO_CONTROLE):
        return set()
    with open(ARQUIVO_CONTROLE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def salvar_enviado(projeto_id):
    with open(ARQUIVO_CONTROLE, 'a') as f:
        f.write(f"{projeto_id}\n")

def send_telegram_message(text):
    if not text: return
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte}
        try:
            requests.post(url_api, json=payload, timeout=30)
        except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")

def main():
    enviados = carregar_enviados()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    erros_consecutivos = 0
    print(f"Iniciando varredura do ID {ID_INICIAL} ao {ID_FINAL}...")

    for projeto_id in range(ID_INICIAL, ID_FINAL + 1):
        if str(projeto_id) in enviados:
            print(f"Projeto {projeto_id} já enviado. Pulando...")
            continue

        url = f"https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx?ProjetoId={projeto_id}&TipoAlvara=2"
        print(f"Analisando Projeto {projeto_id}...")

        try:
            res = requests.get(url, headers=headers, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')

            # --- TRIAGEM PRÉVIA ---
            elemento_titulo = soup.find(id="GoianiaTheme_wt8_block_wtTitle")
            if elemento_titulo and "Erro Interno" in elemento_titulo.get_text():
                erros_consecutivos += 1
                print(f"⚠️ Erro Interno detectado (Consecutivos: {erros_consecutivos})")

                if erros_consecutivos >= 5:
                    msg_alerta = "🛑 Aviso: Execução do bot interrompida. Foram detectados 5 links seguidos com 'Erro Interno' no site da Prefeitura."
                    send_telegram_message(msg_alerta)
                    print("Limite de erros consecutivos atingido. Encerrando o script.")
                    break

                time.sleep(2)
                continue

            # Reset de erros
            erros_consecutivos = 0

            texto_pagina = soup.get_text(separator='\n', strip=True)
            prompt_final = f"{PROMPT_BASE}\n{texto_pagina}\n\nLink: {url}"

            resposta_ia = model.generate_content(prompt_final).text.strip()

            if resposta_ia == "VAZIO":
                print(f"Projeto {projeto_id} ignorado pela IA (sem dados).")
            else:
                send_telegram_message(resposta_ia)
                salvar_enviado(projeto_id)
                print(f"✅ Resumo do Projeto {projeto_id} enviado com sucesso!")

        except Exception as e:
            print(f"❌ Erro ao processar o projeto {projeto_id}: {e}")
        time.sleep(4) 

    print("Varredura concluída!")

    # Salva o histórico no GitHub
    os.system('git config --global user.name "github-actions[bot]"')
    os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    os.system(f'git add {ARQUIVO_CONTROLE}')
    os.system('git diff --staged --quiet || (git commit -m "bot: atualiza projetos processados" && git pull --rebase && git push)')

if __name__ == "__main__":
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and GOOGLE_API_KEY:
        main()
    else:
        print("Erro: Variáveis de ambiente faltando.")
