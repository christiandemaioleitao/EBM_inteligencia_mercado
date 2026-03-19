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
# URL para 2026 conforme original, ajuste se necessário
URL_DIARIOS = "https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp?ano=2026"
ARQUIVO_CONTROLE = "ultimo_diario.txt"

# Variáveis de Ambiente
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# Configuração do Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key="AIzaSyCuR_KJaHuj2-j7IkQ4iDVou4CPkbGNH_Q")
    # Usando o Flash por ser mais rápido e barato para resumos
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("ERRO: Variável GOOGLE_API_KEY não configurada.")
    exit(1)

# ==========================================
# SEU PROMPT PERSONALIZADO
# ==========================================
# Defina aqui exatamente o que você quer que a IA faça com o texto.
# Exemplo focado no objetivo original (Remembramento), mas flexível.
PROMPT_IA = """
Analise o texto completo do Diário Oficial fornecido abaixo.
Seu objetivo é identificar e listar todas as ocorrências de 'Certidões de Remembramento'.

Para cada ocorrência encontrada, extraia estritamente:
1. O nome do Interessado (empresa ou pessoa).
2. O endereço ou localização do imóvel referenciado.
3. Um resumo muito breve (1 frase) do que foi decidido (ex: Aprovado, Indeferido).

Formate a resposta em HTML para o Telegram, usando <b> para títulos e <i> para o conteúdo, como no exemplo:
🏢 <b>Interessado:</b> <i>Nome da Empresa LTDA</i>
📍 <b>Local:</b> <i>Rua X, Qd Y, Goiânia</i>
📝 <b>Decisão:</b> <i>Remembramento Aprovado.</i>
------------------------

Se não encontrar NENHUMA certidão de remembramento, responda apenas: "Nenhum remembramento encontrado."
Não invente dados. Se não encontrar uma informação específica (como o local), pule-a.

TEXTO DO DIÁRIO OFICIAL:
---
"""

def send_telegram_message(text):
    """Envia a mensagem para o Telegram suportando HTML e mensagens longas."""
    if not text: return
    
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Divide a mensagem se for maior que o limite do Telegram (4096 chars)
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    for parte in partes:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": parte, "parse_mode": "HTML"}
        try:
            requests.post(url_api, json=payload, timeout=30)
        except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")

def extrair_texto_pdf(pdf_content):
    """Extrai todo o texto de um PDF em memória."""
    texto_completo = ""
    try:
        pdf_file = io.BytesIO(pdf_content)
        reader = PdfReader(pdf_file)
        for page in reader.pages:
            texto_completo += page.extract_text() + "\n\n"
    except Exception as e:
        print(f"Erro ao ler PDF: {e}")
    return texto_completo

def main():
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 1. Busca link do diário mais recente
    try:
        response = requests.get(URL_DIARIOS, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao acessar lista: {e}"); return

    soup = BeautifulSoup(response.text, 'html.parser')
    link_pdf = None
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'pdf' in href.lower() or 'diario' in href.lower():
            link_pdf = href; break
            
    if not link_pdf:
        print("Nenhum PDF encontrado."); return
        
    url_pdf_completa = requests.compat.urljoin(URL_BASE, link_pdf)

    # 2. Controle de duplicidade
    if os.path.exists(ARQUIVO_CONTROLE):
        with open(ARQUIVO_CONTROLE, 'r') as f:
            if url_pdf_completa == f.read().strip():
                print("Diário já processado."); return

    print(f"Processando novo Diário: {url_pdf_completa}")

    # 3. Baixa e Extrai Texto
    try:
        pdf_response = requests.get(url_pdf_completa, headers=headers, timeout=60)
        pdf_response.raise_for_status()
        texto_diario = extrair_texto_pdf(pdf_response.content)
    except Exception as e:
        print(f"Erro no download/extração: {e}"); return

    if not texto_diario.strip():
        send_telegram_message("⚠️ Novo diário encontrado, mas não foi possível extrair texto do PDF.")
        return

    # 4. Envia para a IA analisar
    print("Enviando texto para análise da IA (isso pode demorar)...")
    try:
        # Junta o prompt configurado com o texto bruto do PDF
        prompt_final = f"{PROMPT_IA}\n\n{texto_diario}"
        
        # O Gemini 1.5 Flash suporta 1 milhão de tokens, cabe qualquer Diário.
        response = model.generate_content(prompt_final)
        analise_ia = response.text
    except Exception as e:
        analise_ia = f"❌ Erro na análise da IA: {e}"

    # 5. Formata Mensagem Final e Envia
    cabecalho = f"🏛️ <b>Análise Inteligente do Diário Oficial</b>\n"
    cabecalho += f"🔗 <a href='{url_pdf_completa}'>Acessar PDF Completo</a>\n"
    cabecalho += "========================\n\n"
    
    send_telegram_message(cabecalho + analise_ia)

    # 6. Atualiza controle
    with open(ARQUIVO_CONTROLE, 'w') as f: f.write(url_pdf_completa)
    print("Concluído.")

if __name__ == "__main__":
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        main()
    else:
        print("Erro: Variáveis do Telegram não configuradas.")
