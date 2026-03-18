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

# Lista reduzida para focar apenas nos dados estruturados
PALAVRAS_CHAVE = [
    "remembramento", 
    "desmembramento"
]

ARQUIVO_CONTROLE = "ultimo_diario.txt"

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
# ==========================================

def send_telegram_message(text):
    """Envia a mensagem para o Telegram, quebrando em partes se necessário."""
    url_api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    for parte in partes:
        payload = {"chat_id": CHAT_ID, "text": parte, "parse_mode": "HTML"}
        resposta = requests.post(url_api, json=payload)
        
        if resposta.status_code != 200:
            print(f"❌ O Telegram recusou a formatação HTML. Motivo: {resposta.text}")
            texto_puro = parte.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '').replace('<a href=', '').replace('</a>', '')
            payload_seguro = {"chat_id": CHAT_ID, "text": "⚠️ [Modo Texto Puro]\n\n" + texto_puro}
            requests.post(url_api, json=payload_seguro)
        else:
            print("✅ Mensagem enviada com sucesso pro Telegram!")

def extract_page_occurrences(texto, palavras_chave, num_pagina):
    """
    Varre a página identificando 'zonas' onde as palavras aparecem. 
    Busca ativamente por Interessado e Endereço para certidões.
    """
    ocorrencias = []
    
    palavras_estruturadas = ['remembramento', 'desmembramento']
    palavras_comuns = [p for p in palavras_chave if p.lower() not in palavras_estruturadas]
    
    # PASSO 1: PROCESSAR BLOCOS ESTRUTURADOS (Prioridade)
    for palavra in palavras_estruturadas:
        if palavra.lower() not in [p.lower() for p in palavras_chave]:
            continue
            
        for match in re.finditer(r'\b' + re.escape(palavra) + r'\b', texto, re.IGNORECASE):
            texto_frente = texto[match.start() : match.start() + 2000]
            
            interessado = None
            endereco = None
            end_pos_bloco = match.end()
            
            # Busca Interessado
            match_int = re.search(r'interesse\s+de\s+(.*?)(?:RESOLVE|Art\.)', texto_frente, re.IGNORECASE)
            if match_int:
                interessado = match_int.group(1).strip()
                end_pos_bloco = max(end_pos_bloco, match.start() + match_int.end())
                
            # Busca Endereço (Atualizado com as preposições "à" e "em")
            match_end = re.search(r'situados?(?:\s+n[ao]\(?s?\)?|\s+à|\s+em)\s+(.*?)(?:,\s*nesta capital|,\s*nesta cidade|,\s*objeto das)', texto_frente, re.IGNORECASE)
            if match_end:
                endereco = match_end.group(1).strip()
                end_pos_bloco = max(end_pos_bloco, match.start() + match_end.end())
            
            if interessado or endereco:
                ctx = f"🏢 <b>Interessado:</b> {interessado or 'Não identificado'}\n📍 <b>Endereço:</b> {endereco or 'Não identificado'}"
                ocorrencias.append({
                    'pagina': num_pagina,
                    'start': match.start() - 200,
                    'end': end_pos_bloco + 300,
                    'texto_exibicao': ctx,
                    'keywords': {palavra.upper()}
                })
            else:
                # Se não achar a estrutura bonitinha, manda a palavra para a lista comum para extrair o contexto em volta
                if palavra not in palavras_comuns:
                    palavras_comuns.append(palavra)
                    
    # PASSO 2: PROCESSAR PALAVRAS COMUNS (Fallback ou palavras adicionais futuras)
    for palavra in palavras_comuns:
        for match in re.finditer(r'\b' + re.escape(palavra) + r'\b', texto, re.IGNORECASE):
            centro_palavra = (match.start() + match.end()) / 2
            ja_coberto = False
            
            for oc in ocorrencias:
                if oc['start'] <= centro_palavra <= oc['end']:
                    oc['keywords'].add(palavra.upper())
                    ja_coberto = True
                    break
            
            if not ja_coberto:
                inicio_idx = max(0, match.start() - 400)
                fim_idx = min(len(texto), match.end() + 400)
                
                while inicio_idx > 0 and texto[inicio_idx] not in [' ', '\n']:
                    inicio_idx -= 1
                while fim_idx < len(texto) and texto[fim_idx] not in [' ', '\n']:
                    fim_idx += 1
                    
                trecho_bruto = texto[inicio_idx:fim_idx].strip()
                ctx = f"...{trecho_bruto}..."
                
                ocorrencias.append({
                    'pagina': num_pagina,
                    'start': inicio_idx,
                    'end': fim_idx,
                    'texto_exibicao': ctx,
                    'keywords': {palavra.upper()}
                })

    return ocorrencias

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
        
    todas_ocorrencias = []
    
    try:
        pdf_file = io.BytesIO(pdf_response.content)
        reader = PdfReader(pdf_file)
        
        for num_pagina, page in enumerate(reader.pages, start=1):
            texto_extraido = page.extract_text()
            
            if texto_extraido:
                texto_limpo = re.sub(r'\s+', ' ', texto_extraido).replace('<', '[').replace('>', ']')
                
                ocorrencias_pagina = extract_page_occurrences(texto_limpo, PALAVRAS_CHAVE, num_pagina)
                if ocorrencias_pagina:
                    todas_ocorrencias.extend(ocorrencias_pagina)
                            
    except Exception as e:
        print(f"Erro ao ler as páginas do PDF: {e}")
        return

    if todas_ocorrencias:
        mensagem_final = f"🏛️ <b>Novo Diário Oficial Analisado!</b>\n"
        mensagem_final += f"🔍 Encontrei <b>{len(todas_ocorrencias)}</b> blocos de informação relevantes.\n\n"
        mensagem_final += "========================\n\n"
        
        for i, oc in enumerate(todas_ocorrencias, 1):
            termos_str = ", ".join(oc['keywords'])
            mensagem_final += f"📌 <b>OCORRÊNCIA {i}</b> (Pág {oc['pagina']})\n"
            mensagem_final += f"🏷️ <i>Termos mapeados: {termos_str}</i>\n\n"
            
            texto_final = oc['texto_exibicao']
            
            if not texto_final.startswith('🏢'):
                for kw in oc['keywords']:
                    texto_final = re.sub(r'\b(' + re.escape(kw) + r')\b', r'<b>\1</b>', texto_final, flags=re.IGNORECASE)
                    
            mensagem_final += f"{texto_final}\n\n"
            mensagem_final += "------------------------\n\n"
        
        mensagem_final += f"🔗 <b>Acessar PDF Completo:</b> <a href='{url_pdf_completa}'>Clique Aqui</a>"
            
        send_telegram_message(mensagem_final)
    else:
        mensagem_vazia = (
            "🏛️ <b>Novo Diário Oficial Analisado!</b>\n"
            "O diário de hoje foi lido, mas nenhum Remembramento ou Desmembramento apareceu.\n\n"
            f"🔗 <b>Acessar PDF:</b> <a href='{url_pdf_completa}'>Clique Aqui</a>"
        )
        send_telegram_message(mensagem_vazia)

    with open(ARQUIVO_CONTROLE, 'w') as f:
        f.write(url_pdf_completa)
    print("Arquivo de controle atualizado.")

if __name__ == "__main__":
    if TOKEN and CHAT_ID:
        main()
    else:
        print("Erro: Variáveis de ambiente do Telegram não configuradas.")
