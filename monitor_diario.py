import os
import requests
import io
from bs4 import BeautifulSoup
from pypdf import PdfReader
import google.generativeai as genai

# ==========================================
# CONFIGURAÇÕES E AMBIENTE
# ==========================================
URL_BASE = "https://www.goiania.go.gov.br"
URL_DIARIOS = "https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp?ano=2026"
ARQUIVO_CONTROLE = "ultimo_diario.txt"

# Variáveis de Ambiente (Puxadas do GitHub Actions)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# Configuração do Gemini
if GOOGLE_API_KEY:
    # CORREÇÃO: Agora usa a variável de ambiente, não a chave exposta
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("ERRO: Variável GOOGLE_API_KEY não configurada nos Secrets do GitHub.")
    exit(1)

# ==========================================
# PROMPT DA IA
# ==========================================
PROMPT_IA = """
Analise o texto do Diário Oficial abaixo.
Identifique 'Certidões de Remembramento'.
Para cada uma, extraia:
1. Interessado
2. Endereço/Localização
3. Resumo da decisão

Formate em HTML para Telegram:
🏢 <b>Interessado:</b> <i>Nome</i>
📍 <b>Local:</b> <i>Endereço</i>
📝 <b>Decisão:</b> <i>Resumo</i>
------------------------
Se não encontrar nada, responda: "Nenhum remembramento encontrado."

TEXTO:
---
"""

def send_telegram_message(text):
    if not text: return
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "HTML"}
        try:
            requests.post(url_api, json=payload, timeout=30)
        except Exception as e:
            print(f"Erro Telegram: {e}")

def extrair_texto_pdf(pdf_content):
    texto_completo = ""
    try:
        pdf_file = io.BytesIO(pdf_content)
        reader = PdfReader(pdf_file)
        for page in reader.pages:
            texto_extraido = page.extract_text()
            if texto_extraido:
                texto_completo += texto_extraido + "\n\n"
    except Exception as e:
        print(f"Erro ao ler PDF: {e}")
    return texto_completo

def main():
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(URL_DIARIOS, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        link_pdf = None
        for a in soup.find_all('a', href=True):
            if 'pdf' in a['href'].lower() or 'exibe' in a['href'].lower():
                link_pdf = a['href']
                break
        
        if not link_pdf:
            print("Nenhum PDF novo encontrado."); return

        url_pdf_completa = requests.compat.urljoin(URL_BASE, link_pdf)

        if os.path.exists(ARQUIVO_CONTROLE):
            with open(ARQUIVO_CONTROLE, 'r') as f:
                if url_pdf_completa == f.read().strip():
                    print("Diário já processado anteriormente."); return

        print(f"Baixando: {url_pdf_completa}")
        pdf_res = requests.get(url_pdf_completa, headers=headers, timeout=60)
        pdf_res.raise_for_status()
        
        texto_diario = extrair_texto_pdf(pdf_res.content)
        
        if not texto_diario.strip():
            print("PDF sem texto extraível."); return

        # Chamada da IA
        response_ia = model.generate_content(f"{PROMPT_IA}\n\n{texto_diario}")
        analise = response_ia.text

        msg = f"🏛️ <b>Novo Diário Oficial Analisado</b>\n🔗 <a href='{url_pdf_completa}'>Link do PDF</a>\n\n{analise}"
        send_telegram_message(msg)

        with open(ARQUIVO_CONTROLE, 'w') as f:
            f.write(url_pdf_completa)

    except Exception as e:
        print(f"Erro geral: {e}")

if __name__ == "__main__":

    print(f"Texto extraído (primeiros 100 caracteres): {texto_diario[:100]}")
    print(f"Resposta da IA: {analise}")
    main()
