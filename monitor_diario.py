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

RANGE_PALAVRAS = 50 
ARQUIVO_CONTROLE = "ultimo_diario.txt"
# ==========================================

# Pegando as credenciais do ambiente
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_message(text):
    """Envia a mensagem para o Telegram e tem um plano B caso o Telegram bloqueie a formatação."""
    url_api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    for parte in partes:
        # Tentativa 1: Enviar com formatação bonita (HTML)
        payload = {"chat_id": CHAT_ID, "text": parte, "parse_mode": "HTML"}
        resposta = requests.post(url_api, json=payload)
        
        # Se o Telegram recusar (geralmente por causa de algum caractere estranho no PDF)
        if resposta.status_code != 200:
            print(f"❌ O Telegram recusou a formatação HTML. Motivo: {resposta.text}")
            print("⚠️ Tentando enviar a mensagem em texto puro (Plano B)...")
            
            # Tentativa 2: Remove a exigência de HTML e manda texto puro
            texto_puro = parte.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '').replace('<a href=', '').replace('</a>', '')
            payload_seguro = {"chat_id": CHAT_ID, "text": "⚠️ [Modo Texto Puro]\n\n" + texto_puro}
            requests.post(url_api, json=payload_seguro)
        else:
            print("✅ Mensagem enviada com sucesso pro Telegram!")

def extract_context(texto, palavra, num_palavras):
    regex = r'((?:\S+\s+){0,' + str(num_palavras) + r'})(' + re.escape(palavra) + r')((?:\s+\S+){0,' + str(num_palavras) + r'})'
    padrao = re.compile(regex, re.IGNORECASE)
    matches = padrao.finditer(texto)
    resultados = []
    for match in matches:
        antes = match.group(1).strip()
        termo = match.group(2).strip()
        depois = match.group(3).strip()
        contexto = f"...{antes} <b>{termo.upper()}</b> {depois}..."
        resultados.append(contexto)
    return resultados

def main():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        response = requests.get(URL_DIARIOS, headers=headers)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao acessar a lista de diários: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    link_pdf = None
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'pdf' in href.lower() or 'diario' in href.lower() or 'download' in href.lower() or 'exibe' in href.lower():
            link_pdf = href
            break
            
    if not link_pdf:
        print("Nenhum link de diário encontrado na página.")
        return
        
    if link_pdf.startswith('/'):
        url_pdf_completa = URL_BASE + link_pdf
    elif not link_pdf.startswith('http'):
        url_pdf_completa = URL_BASE + "/shtml//portal/casacivil/" + link_pdf
    else:
        url_pdf_completa = link_pdf

    if os.path.exists(ARQUIVO_CONTROLE):
        with open(ARQUIVO_CONTROLE, 'r') as f:
            ultimo_lido = f.read().strip()
        if url_pdf_completa == ultimo_lido:
            print("Este diário já foi processado anteriormente. Encerrando.")
            return

    print(f"Baixando novo Diário Oficial: {url_pdf_completa}")

    try:
        pdf_response = requests.get(url_pdf_completa, headers=headers)
        pdf_response.raise_for_status()
    except Exception as e:
        print(f"Erro ao baixar o PDF: {e}")
        return
        
    resultados_encontrados = {}
    total_ocorrencias = 0
    
    try:
        pdf_file = io.BytesIO(pdf_response.content)
        reader = PdfReader(pdf_file)
        
        for num_pagina, page in enumerate(reader.pages, start=1):
            texto_extraido = page.extract_text()
            
            if texto_extraido:
                # Limpeza extrema: remove quebras de linha e substitui sinais de < e > por colchetes
                texto_limpo = re.sub(r'\s+', ' ', texto_extraido).replace('<', '[').replace('>', ']')
                
                for palavra in PALAVRAS_CHAVE:
                    contextos = extract_context(texto_limpo, palavra, RANGE_PALAVRAS)
                    if contextos:
                        if palavra not in resultados_encontrados:
                            resultados_encontrados[palavra] = []
                        
                        for ctx in contextos:
                            resultados_encontrados[palavra].append({
                                'texto': ctx,
                                'pagina': num_pagina
                            })
                            total_ocorrencias += 1
                            
    except Exception as e:
        print(f"Erro ao ler as páginas do PDF: {e}")
        return

    if resultados_encontrados:
        mensagem_final = f"🏛️ <b>Novo Diário Oficial Analisado!</b>\n"
        mensagem_final += f"🔍 Encontrei <b>{total_ocorrencias}</b> ocorrências das suas palavras-chave.\n\n"
        mensagem_final += "========================\n\n"
        
        for palavra, ocorrencias in resultados_encontrados.items():
            mensagem_final += f"🎯 <b>TERMO ENCONTRADO: '{palavra.upper()}'</b> ({len(ocorrencias)} vezes)\n\n"
            
            for i, item in enumerate(ocorrencias[:5]):
                mensagem_final += f"📄 <b>[Pág {item['pagina']}]</b> <i>{item['texto']}</i>\n\n"
            
            if len(ocorrencias) > 5:
                mensagem_final += f"⚠️ <i>Mais {len(ocorrencias) - 5} ocorrência(s) desta palavra foram omitidas.</i>\n\n"
            
            mensagem_final += "------------------------\n\n"
        
        mensagem_final += f"🔗 <b>Acessar PDF Completo:</b> <a href='{url_pdf_completa}'>Clique Aqui</a>"
            
        send_telegram_message(mensagem_final)
    else:
        mensagem_vazia = (
            "🏛️ <b>Novo Diário Oficial Analisado!</b>\n"
            "O diário de hoje foi lido, mas nenhuma das suas palavras-chave apareceu.\n\n"
            f"🔗 <b>Acessar PDF:</b> <a href='{url_pdf_completa}'>Clique Aqui</a>"
        )
        send_telegram_message(mensagem_vazia)

    # Atualiza o controle apenas no final do processo
    with open(ARQUIVO_CONTROLE, 'w') as f:
        f.write(url_pdf_completa)
    print("Arquivo de controle atualizado.")

if __name__ == "__main__":
    if TOKEN and CHAT_ID:
        main()
    else:
        print("Erro: Variáveis de ambiente do Telegram não configuradas.")
