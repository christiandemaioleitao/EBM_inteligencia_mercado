import os
import re
import requests
import io
from bs4 import BeautifulSoup
from pypdf import PdfReader

# ==========================================
# CONFIGURAÇÕES FÁCEIS DE EDITAR
# ==========================================
URL_BASE = "https://www.goiania.go.gov.br"
URL_DIARIOS = "https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp?ano=2026"

PALAVRAS_CHAVE = [
    "SPE", "remembramento", "desmembramento", 
    "aprovação de projeto", "opus", "incorporação", 
    "demolição", "city", "brasil incorporação"
]

# Quantidade de palavras antes e depois do termo encontrado
RANGE_PALAVRAS = 50 

ARQUIVO_CONTROLE = "ultimo_diario.txt"
# ==========================================

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(text):
    """Envia a mensagem para o Telegram, dividindo em partes se for muito grande."""
    url_api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    # Se o texto for maior que o limite do Telegram, quebra em partes de 4000 caracteres
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for parte in partes:
        payload = {"chat_id": CHAT_ID, "text": parte, "parse_mode": "HTML"}
        requests.post(url_api, json=payload)

def extract_context(texto, palavra, num_palavras):
    """Usa Regex para pegar X palavras antes e depois da palavra-chave encontrada."""
    # A regex busca até 'num_palavras' palavras antes, a palavra exata, e até 'num_palavras' depois
    regex = r'((?:\S+\s+){0,' + str(num_palavras) + r'})(' + re.escape(palavra) + r')((?:\s+\S+){0,' + str(num_palavras) + r'})'
    padrao = re.compile(regex, re.IGNORECASE)
    
    matches = padrao.finditer(texto)
    resultados = []
    
    for match in matches:
        antes = match.group(1).strip()
        termo = match.group(2).strip()
        depois = match.group(3).strip()
        # Formata o trecho encontrado deixando o termo em negrito
        contexto = f"...{antes} <b>{termo.upper()}</b> {depois}..."
        resultados.append(contexto)
        
    return resultados

def main():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 1. Acessa a página da Casa Civil de Goiânia
    try:
        response = requests.get(URL_DIARIOS, headers=headers)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao acessar a lista de diários: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 2. Busca o primeiro link da página que leve para um PDF ou para a exibição do diário
    link_pdf = None
    for a in soup.find_all('a', href=True):
        href = a['href']
        # Identifica links que geralmente apontam para o Diário Oficial
        if 'pdf' in href.lower() or 'diario' in href.lower() or 'download' in href.lower() or 'exibe' in href.lower():
            link_pdf = href
            break
            
    if not link_pdf:
        print("Nenhum link de diário encontrado na página.")
        return
        
    # Arruma a URL caso ela seja relativa (comece com / ou sem o http)
    if link_pdf.startswith('/'):
        url_pdf_completa = URL_BASE + link_pdf
    elif not link_pdf.startswith('http'):
        url_pdf_completa = URL_BASE + "/shtml//portal/casacivil/" + link_pdf
    else:
        url_pdf_completa = link_pdf

    # 3. Verifica se este Diário já foi lido ontem
    if os.path.exists(ARQUIVO_CONTROLE):
        with open(ARQUIVO_CONTROLE, 'r') as f:
            ultimo_lido = f.read().strip()
        if url_pdf_completa == ultimo_lido:
            print("Este diário já foi processado anteriormente. Encerrando.")
            return

    print(f"Baixando novo Diário Oficial: {url_pdf_completa}")

    # 4. Baixa o PDF para a memória
    try:
        pdf_response = requests.get(url_pdf_completa, headers=headers)
        pdf_response.raise_for_status()
    except Exception as e:
        print(f"Erro ao baixar o PDF: {e}")
        return
        
    # 5. Extrai o texto de todas as páginas do PDF
    try:
        pdf_file = io.BytesIO(pdf_response.content)
        reader = PdfReader(pdf_file)
        texto_completo = ""
        for page in reader.pages:
            texto_extraido = page.extract_text()
            if texto_extraido:
                texto_completo += texto_extraido + " "
    except Exception as e:
        print(f"Erro ao ler as páginas do PDF: {e}")
        return
        
    # Substitui quebras de linha e múltiplos espaços por um espaço simples para não quebrar a formatação
    texto_limpo = re.sub(r'\s+', ' ', texto_completo)
    
    # 6. Procura as palavras-chave no texto
    resultados_encontrados = {}
    total_ocorrencias = 0
    
    for palavra in PALAVRAS_CHAVE:
        contextos = extract_context(texto_limpo, palavra, RANGE_PALAVRAS)
        if contextos:
            resultados_encontrados[palavra] = contextos
            total_ocorrencias += len(contextos)

    # 7. Formata e envia a mensagem para o Telegram
    if resultados_encontrados:
        mensagem_final = f"🏛️ <b>Novo Diário Oficial Analisado!</b>\n"
        mensagem_final += f"🔍 Tivemos <b>{total_ocorrencias}</b> ocorrências das suas palavras-chave.\n"
        mensagem_final += f"🔗 <a href='{url_pdf_completa}'>Abrir PDF Original</a>\n\n"
        mensagem_final += "========================\n\n"
        
        for palavra, contextos in resultados_encontrados.items():
            mensagem_final += f"🎯 <b>TERMO ENCONTRADO: '{palavra.upper()}'</b> ({len(contextos)} vezes)\n\n"
            
            # Limita a 5 trechos por palavra-chave para a mensagem não ficar gigantesca
            for i, ctx in enumerate(contextos[:5]):
                mensagem_final += f"<i>{ctx}</i>\n\n"
            
            if len(contextos) > 5:
                mensagem_final += f"⚠️ <i>Mais {len(contextos) - 5} ocorrência(s) desta palavra foram omitidas. Abra o PDF para ler tudo.</i>\n\n"
            
            mensagem_final += "------------------------\n\n"
                
        send_telegram_message(mensagem_final)
        print("Resultados enviados para o Telegram.")
    else:
        # Opcional: Avisar que leu, mas não achou nada
        send_telegram_message(f"🏛️ <b>Novo Diário Oficial:</b>\nO diário de hoje foi lido, mas nenhuma das suas palavras-chave apareceu.\n🔗 <a href='{url_pdf_completa}'>Link do Diário</a>")
        print("Diário lido, mas nenhuma palavra-chave encontrada.")

    # 8. Atualiza o controle para não ler este PDF amanhã de novo
    with open(ARQUIVO_CONTROLE, 'w') as f:
        f.write(url_pdf_completa)

if __name__ == "__main__":
    main()
