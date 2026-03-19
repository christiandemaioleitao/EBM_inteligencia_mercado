import os
import requests
import io
import datetime
from bs4 import BeautifulSoup
from pypdf import PdfReader
import google.generativeai as genai

# ==========================================
# CONFIGURAÇÕES E AMBIENTE
# ==========================================
URL_BASE = "https://www.goiania.go.gov.br"

# Pega o ano atual automaticamente do sistema
ANO_ATUAL = datetime.date.today().year
URL_DIARIOS = f"https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp?ano={ANO_ATUAL}"

# Variáveis de Ambiente (Puxadas dos Secrets do GitHub)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# Configuração da API do Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("ERRO: Variável GOOGLE_API_KEY não configurada.")
    exit(1)

# ==========================================
# PROMPT DA IA
# ==========================================
PROMPT_IA = """
Analise o texto do Diário Oficial de Goiânia abaixo.
Identifique 'Certidões de Remembramento'.
Para cada uma, extraia:
1. Interessado
2. Endereço/Localização do imóvel
3. Resumo da decisão

Formate em HTML para Telegram:
🏢 <b>Interessado:</b> <i>Nome</i>
📍 <b>Local:</b> <i>Endereço</i>
📝 <b>Decisão:</b> <i>Resumo</i>
------------------------
Se não encontrar nada, responda apenas: "Nenhum remembramento encontrado no diário de hoje."

TEXTO:
---
"""

def send_telegram_message(text):
    """Envia a mensagem para o Telegram quebrando em partes se for muito longa."""
    if not text: return
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "HTML"}
        try:
            requests.post(url_api, json=payload, timeout=30)
        except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")

def extrair_texto_pdf(pdf_content):
    """Lê o PDF em memória e extrai o texto página por página."""
    texto_completo = ""
    try:
        pdf_file = io.BytesIO(pdf_content)
        reader = PdfReader(pdf_file)
        for page in reader.pages:
            t = page.extract_text()
            if t: texto_completo += t + "\n"
    except Exception as e:
        print(f"Erro na extração do PDF: {e}")
    return texto_completo

def main():
    headers = {'User-Agent': 'Mozilla/5.0'}
    print(f"Buscando link do PDF para o ano {ANO_ATUAL}...")
    
    try:
        res = requests.get(URL_DIARIOS, headers=headers, timeout=30)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        
        link_pdf = None
        for a in soup.find_all('a', href=True):
            if 'pdf' in a['href'].lower() or 'exibe' in a['href'].lower():
                link_pdf = a['href']
                break
        
        if not link_pdf:
            send_telegram_message(f"❌ Não encontrei nenhum PDF de diário na página de {ANO_ATUAL}.")
            return

        url_completa = requests.compat.urljoin(URL_BASE, link_pdf)
        print(f"Baixando: {url_completa}")
        
        pdf_res = requests.get(url_completa, headers=headers, timeout=60)
        pdf_res.raise_for_status()
        
        print("Extraindo texto...")
        texto = extrair_texto_pdf(pdf_res.content)
        
        if not texto.strip():
            send_telegram_message(f"⚠️ PDF encontrado, mas não consegui extrair o texto.\nLink: {url_completa}")
            return

        print("Solicitando análise à IA...")
        response_ia = model.generate_content(f"{PROMPT_IA}\n\n{texto}")
        analise = response_ia.text

        mensagem = f"🏛️ <b>Análise do Diário Oficial ({ANO_ATUAL})</b>\n🔗 <a href='{url_completa}'>Link do PDF</a>\n\n{analise}"
        send_telegram_message(mensagem)
        print("Processo finalizado com sucesso!")

    except Exception as e:
        erro_msg = f"❌ Erro crítico no script do Diário Oficial:\n{str(e)}"
        print(erro_msg)
        send_telegram_message(erro_msg)

if __name__ == "__main__":
    # Garante que não vai rodar sem as chaves
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and GOOGLE_API_KEY:
        main()
    else:
        print("Erro: Variáveis de ambiente faltando. Configure os Secrets no GitHub.")
