import os
import re
import requests
import io
import unicodedata
from bs4 import BeautifulSoup
from pypdf import PdfReader

# ==========================================
# CONFIGURACOES
# ==========================================
URL_BASE = "https://www.goiania.go.gov.br"
URL_DIARIOS = "https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp?ano=2026"

ARQUIVO_CONTROLE = "ultimo_diario.txt"

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
# ==========================================

def remover_acentos(texto):
    """Remove todos os acentos do texto (ex: à->a, ç->c, ã->a)"""
    if not texto:
        return ""
    # Transforma os caracteres e ignora/remove os acentos
    return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('utf-8')

def send_telegram_message(text):
    """Envia a mensagem para o Telegram"""
    url_api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    # Textos já estão sem acento, o que evita o corrompimento no envio
    partes = [text[i:i+4000] for i in range(0, len(text), 4000)]
    
    for parte in partes:
        payload = {"chat_id": CHAT_ID, "text": parte, "parse_mode": "HTML"}
        resposta = requests.post(url_api, json=payload)
        
        if resposta.status_code != 200:
            print(f"ERRO DO TELEGRAM: {resposta.text}")
            texto_puro = parte.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '').replace('<a href=', '').replace('</a>', '')
            payload_seguro = {"chat_id": CHAT_ID, "text": "[Modo Texto Puro]\n\n" + texto_puro}
            requests.post(url_api, json=payload_seguro)
        else:
            print("Mensagem enviada com sucesso pro Telegram!")

def extrair_blocos_remembramento(texto_sem_acento, num_pagina):
    """
    Busca os trechos que comecam com 'Art' e terminam em mais de 2 espacos.
    Valida se o trecho + cabecalho possuem as 3 frases chaves.
    """
    ocorrencias = []
    
    # REGRA DE CORTE: Comeca em "Art" e captura tudo ate achar 3 ou mais espacos seguidos (\s{3,})
    padrao_art = re.compile(r'\b(Art[\.\s].*?)(?=\s{3,}|\Z)', re.IGNORECASE | re.DOTALL)
    
    for match in padrao_art.finditer(texto_sem_acento):
        bloco_art_bruto = match.group(1)
        
        # Pega uma "janela" que inclui o texto antes do Artigo (para poder ler o cabecalho e o interessado)
        inicio_janela = max(0, match.start() - 2000)
        janela_validacao = texto_sem_acento[inicio_janela : match.end()]
        janela_lower = janela_validacao.lower()
        
        # REGRA DE GATILHO DUPLO: Tem que ter as 3 frases no bloco analisado
        tem_certidao = "certidao de remembramento" in janela_lower
        tem_aprovado = "aprovado o remembramento" in janela_lower
        tem_situacao = "situacao atual" in janela_lower
        
        if tem_certidao and tem_aprovado and tem_situacao:
            
            # 1. Extrai o Interessado (tudo que vem apos "interesse de" ou "interessado:" ate achar RESOLVE ou Art)
            interessado = "Nao identificado"
            match_int = re.search(r'(?:interesse\s+de|interessad[oa]s?[\s:]+)(.*?)(?:RESOLVE|\bArt\b)', janela_validacao, re.IGNORECASE | re.DOTALL)
            if match_int:
                # Retira as quebras de linha de dentro do nome da empresa para ficar limpo
                interessado = re.sub(r'\s+', ' ', match_int.group(1)).strip()
                
            # 2. Limpa o trecho capturado para remover quebras de linha e apresentar como um paragrafo unico no Telegram
            trecho_limpo = re.sub(r'\s+', ' ', bloco_art_bruto).strip()
            
            # Monta o contexto que sera enviado na mensagem
            ctx = f"🏢 <b>Interessado:</b> {interessado}\n📄 <b>Trecho:</b> {trecho_limpo}"
            
            ocorrencias.append({
                'pagina': num_pagina,
                'texto_exibicao': ctx
            })
            
    return ocorrencias

def main():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        response = requests.get(URL_DIARIOS, headers=headers)
        response.raise_for_status()
    except Exception as e:
        print(f"Erro ao acessar a lista de diarios: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    link_pdf = None
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'pdf' in href.lower() or 'diario' in href.lower() or 'download' in href.lower() or 'exibe' in href.lower():
            link_pdf = href
            break
            
    if not link_pdf:
        print("Nenhum link de diario encontrado na pagina.")
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
            print("Este diario ja foi processado anteriormente. Encerrando.")
            return

    print(f"Baixando novo Diario Oficial: {url_pdf_completa}")

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
                # Passo fundamental: remover acentos antes de submeter a analise
                texto_sem_acento = remover_acentos(texto_extraido)
                
                ocorrencias_pagina = extrair_blocos_remembramento(texto_sem_acento, num_pagina)
                if ocorrencias_pagina:
                    todas_ocorrencias.extend(ocorrencias_pagina)
                            
    except Exception as e:
        print(f"Erro ao ler as paginas do PDF: {e}")
        return

    # Mensagens de alerta tambem geradas sem acento para garantir compatibilidade
    if todas_ocorrencias:
        mensagem_final = f"🏛️ <b>Novo Diario Oficial Analisado!</b>\n"
        mensagem_final += f"🔍 Encontrei <b>{len(todas_ocorrencias)}</b> certidoes de remembramento validadas.\n\n"
        mensagem_final += "========================\n\n"
        
        for i, oc in enumerate(todas_ocorrencias, 1):
            mensagem_final += f"📌 <b>OCORRENCIA {i}</b> (Pag {oc['pagina']})\n"
            mensagem_final += f"{oc['texto_exibicao']}\n\n"
            mensagem_final += "------------------------\n\n"
        
        mensagem_final += f"🔗 <b>Acessar PDF Completo:</b> <a href='{url_pdf_completa}'>Clique Aqui</a>"
            
        send_telegram_message(mensagem_final)
    else:
        mensagem_vazia = (
            "🏛️ <b>Novo Diario Oficial Analisado!</b>\n"
            "O diario de hoje foi lido, mas nenhum Remembramento com o padrao estabelecido foi encontrado.\n\n"
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
        print("Erro: Variaveis de ambiente do Telegram nao configuradas.")
