import os
import json
import requests
from bs4 import BeautifulSoup

# Credenciais capturadas via Secrets do GitHub
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
STATE_FILE = 'empreendimentos.json'

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)

def load_state():
    """Carrega a lista histórica para garantir que nenhum empreendimento seja perdido."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"mrv": [], "tenda": []}

def save_state(state):
    """Salva o estado atualizado com os novos projetos identificados."""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=4)

def get_tenda_projects():
    url = "https://tenda.com/apartamentos-a-venda/go"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    projects = []
    # Analisando o HTML enviado, os nomes ficam nas tags <h2> dentro desta classe
    titles = soup.find_all('div', class_='empreedimento-title')
    for t in titles:
        name = t.find('h2').text.strip()
        if name and name not in projects:
            projects.append(name)
    return projects

def get_mrv_projects():
    # Placeholder: Para o site da MRV (React), insira aqui o request direto para a API deles.
    # Exemplo:
    # url = "https://www.mrv.com.br/api/imoveis?cidade=goiania&estado=goias"
    # response = requests.get(url).json()
    # return [imovel['nome'] for imovel in response['lista']]
    return []

def main():
    # 1. Carrega o histórico salvo
    state = load_state()
    
    # 2. Faz o scraping das páginas
    try:
        current_tenda = get_tenda_projects()
    except Exception as e:
        print(f"Erro ao extrair dados da Tenda: {e}")
        current_tenda = state.get("tenda", [])

    try:
        current_mrv = get_mrv_projects()
    except Exception as e:
        print(f"Erro ao extrair dados da MRV: {e}")
        current_mrv = state.get("mrv", [])

    # 3. Compara o raspado hoje contra a lista histórica
    new_tenda = [p for p in current_tenda if p not in state.get("tenda", [])]
    new_mrv = [p for p in current_mrv if p not in state.get("mrv", [])]

    # 4. Notifica via Telegram e adiciona os novos à base
    if new_tenda:
        msg = "🏢 <b>Novos empreendimentos da Tenda em Goiás:</b>\n" + "\n".join([f"- {p}" for p in new_tenda])
        send_telegram_message(msg)
        state["tenda"].extend(new_tenda)
        
    if new_mrv:
        msg = "🏢 <b>Novos empreendimentos da MRV em Goiânia:</b>\n" + "\n".join([f"- {p}" for p in new_mrv])
        send_telegram_message(msg)
        state["mrv"].extend(new_mrv)

    # 5. Salva o arquivo atualizado
    save_state(state)

if __name__ == "__main__":
    main()
