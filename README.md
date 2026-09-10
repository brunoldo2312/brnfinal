# 💼 Moeda Bruno (BRN) - Carteira Avançada & Blockchain P2P

A **Moeda Bruno (BRN)** é uma implementação experimental de um ecossistema de criptomoeda descentralizado baseado em princípios acadêmicos do protocolo *CryptoNote/Monero*. O projeto apresenta uma arquitetura modular com um livro-razão imutável, sincronização autônoma de nós Peer-to-Peer (P2P), propagação de transações via Mempool Broadcast e um utilitário automático de redirecionamento de portas (UPnP).

---

## 🚀 Funcionalidades

*   **Consenso de Maior Cadeia (*Longest Chain Rule*):** Algoritmo de resolução de consenso que substitui atomicamente a cadeia local se um par remoto apresentar uma blockchain estritamente mais longa e válida.
*   **Gênese Determinístico (Cadeia Única):** O bloco 0 tem timestamp, nonce e hash fixos (`GENESIS_HASH`), então **todo computador cria exatamente a mesma gênese** e todos compartilham **uma única blockchain**. Nós com gênese diferente (cadeias antigas/divergentes) são recusados na sincronização.
*   **Mempool Persistente com Gossip:** Transações pendentes são gravadas em disco (`mempool_node_<porta>.json`), sobrevivem a reinicializações, são retransmitidas aos demais pares (relay) e removidas automaticamente quando confirmadas em bloco ou após sincronização de cadeia. Transações recebidas com remetente ainda sem saldo local (nó atrasado) vão para uma fila de órfãs (`*.orphan.json`) e são promovidas assim que a cadeia se atualiza.
*   **Livro-Razão Relacional (SQLite3):** Persistência imutável indexada com auditoria histórica e reconstituição dinâmica de saldos em tempo real.
*   **Backup Criptografado de Chaves (.wallet):** Cifragem simétrica em fluxo utilizando derivação de chaves PBKDF de 5000 rounds para proteção de Spend Keys locais.
*   **Interface Gráfica Nativa (Desktop Puro):** Janela escura desacoplada construída sobre a ponte de injeção JavaScript-Python (`pywebview` + `PyQt6`).
*   **Endereço de Recebimento:** Botão para copiar o endereço público da carteira com um clique, facilitando o recebimento de BRN sem expor a chave privada.

---

## 📁 Estrutura Modular do Projeto

O ecossistema foi dividido em módulos isolados para garantir a consistência de dados, mitigar erros de tokenização e otimizar o tempo de compilação:

```text
├── bruno_blockchain_real.py  # Motor principal, API de controle, mempool e chamadas P2P
├── cripto_db.py              # Camada de persistência relacional e queries ordinais SQLite3
├── cripto_wallet.py          # Gerenciador de backup e criptografia PBKDF simétrica
├── index.html                # Interface visual baseada na ponte Javascript-Python Native
├── blockchain_node_<porta>.db   # Cadeia de blocos do nó (SQLite)
├── mempool_node_<porta>.json    # Transações pendentes do nó (persistidas em disco)
└── wallets/                     # Backups .wallet criptografados
```

### Receber BRN

1. Crie ou importe uma carteira.
2. Abaixo de **Seu Endereço Público**, clique em **Copiar endereço de recebimento**.
3. Envie somente esse endereço `brn1...` para quem fará o depósito. Nunca compartilhe a chave privada.

## Segurança e limites do protótipo

Esta é uma blockchain educacional, não indicada para valores reais. A versão atual valida a assinatura contra o endereço do remetente, impede gasto duplo na mempool, rejeita blocos/cadeias com gastos sem saldo e limita mensagens P2P recebidas.

Backups novos usam `cryptography` (Fernet com PBKDF2-SHA256 e 600.000 iterações), exigem senha de ao menos 12 caracteres e são gravados na pasta `wallets/`. Backups antigos Fernet ainda podem ser importados e devem ser reexportados. Instale as dependências antes de iniciar:

```bash
pip install ecdsa cryptography pywebview pyqt6
```

O UPnP deixou de ser ativado automaticamente. Só exponha a porta da carteira à internet se você entender e aceitar esse risco; para testes na mesma rede, use a sincronização manual da interface.

---

## 🛠️ Como Executar o Projeto (Máquina Local)

### 1. Preparação do Ambiente e Dependências (Linux Ubuntu)
Abra o terminal no diretório do projeto e execute os comandos para instalar as bibliotecas de sistema e isolar o ambiente virtual:

```bash
# Instalar pacotes de sistema necessários
sudo apt update && sudo apt install python3-venv python3-full python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 -y

# Criar e ativar o ambiente virtual (VENV)
python3 -m venv env
source env/bin/activate

# Instalar dependências de execução e o motor gráfico isolado PyQt6
pip install --upgrade pip
pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
```

### 2. Inicialização do Nó Principal
Sempre limpe os bancos de dados corrompidos ou inconsistentes de sessões anteriores antes de iniciar o nó na porta de sua escolha (Ex: `6001`):

```bash
rm -rf __pycache__
rm -f *.db mempool_node_*.json
python3 bruno_blockchain_real.py 6001
```

---

## 🌐 Sincronização entre Máquinas Físicas Diferentes

Para rodar a Moeda Bruno em múltiplos computadores conectados na mesma rede Wi-Fi ou cabo:

### 1. Identificar o IP da Máquina Principal
No terminal do seu nó principal (Ex: Seu HP Pavilion), execute:
```bash
hostname -I
# Retornará algo como: 192.168.0.17
```

### 2. Executar o Nó na Segunda Máquina

> **IMPORTANTE — cadeia única:** antes de iniciar, apague os bancos antigos/divergentes em **todos** os computadores (`blockchain_node_*.db`, `mempool_node_*.json`). Cadeias criadas por versões anteriores têm gênese com hash diferente e **não sincronizam** (o nó avisa "gênese pertence a outra rede"). Com a gênese determinística, cada nó recria o mesmo bloco 0 e todos passam a compartilhar **uma única blockchain**.

Copie os arquivos do projeto para o segundo computador. Abra o terminal dele e inicie o script alterando a porta de escuta para não gerar conflitos:

*   **No Linux:** `python3 bruno_blockchain_real.py 6002`
*   **No Windows (CMD Administrador):** 
    ```cmd
    python -m venv env
    .\env\Scripts\activate
    pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
    python bruno_blockchain_real.py 6002
    ```

### 3. Sincronizar as Cadeias de Blocos
1. Vá até a tela do aplicativo na **Segunda Máquina**.
2. No painel superior rosa (**Rede Descentralizada**), insira o IP do seu nó principal: `192.168.0.17`.
3. Defina a porta remota do nó principal: `6001`.
4. Clique em **"Conectar e Sincronizar Cadeira"**. O ecossistema fará o download e a verificação criptográfica do livro-razão automaticamente.

*Nota de Firewall:* A conexão P2P precisa de **porta de entrada liberada nos dois computadores**.
- **Windows (CMD Administrador):** `netsh advfirewall firewall add rule name="Bruno BRN 6001" dir=in action=allow protocol=TCP localport=6001` (troque 6001 pela porta do nó; faça em cada máquina para a sua porta).
- **Linux:** `sudo ufw allow 6001/tcp`.

### 4. Os dois computadores DEVEM compartilhar a mesma blockchain

Se um micro "envia mas o outro não recebe", quase sempre é porque estão em cadeias diferentes. Checklist:

1. **Código atualizado nos dois:** copie `bruno_blockchain_real.py`, `cripto_db.py`, `cripto_wallet.py` e `index.html` atualizados para o outro computador. Nó com código antigo cria gênese divergente.
2. **Convergência automática:** ao iniciar com o código novo, se o banco local tiver gênese antiga/divergente o nó faz backup (`*.divergent-*.bak`) e recria a gênese padrão `0000d904…`. Não é preciso apagar nada manualmente.
3. **Confirme na tela:** o card "🌐 Rede" mostra `🟢 cadeia única compartilhada` com a gênese. Se aparecer `🔴 cadeia divergente`, reinicie o nó. Os dois computadores devem mostrar o **mesmo** hash de gênese.
4. **Firewall nos dois sentidos:** cada máquina deve aceitar entrada na própria porta (veja acima).
5. **Mesma rede:** os dois IPs devem se enxergar (mesmo Wi-Fi/cabo). Teste com `ping <IP-do-outro>`.
6. **Sincronize:** em um dos nós, informe o IP/porta do outro e clique em "Conectar e Sincronizar". A cadeia maior vence e ambos ficam idênticos; transações pendentes se propagam via mempool/gossip.

---

## 📦 Como Gerar o Executável Binário (.App / .Exe)

Para distribuir a carteira de privacidade como um aplicativo desktop nativo e independente (sem a necessidade de instalação prévia do Python na máquina de destino):

### No Linux (Gera binário executável nativo)
```bash
pip install pyinstaller
pyinstaller --onefile --add-data "index.html:." --windowed bruno_blockchain_real.py

# Para executar o binário gerado na pasta dist/
cd dist
chmod +x bruno_blockchain_real
./bruno_blockchain_real 6001
```

### No Windows (Gera o arquivo executável .exe)
Abra o Prompt de Comando (CMD) do Windows dentro da pasta do projeto e execute:
```cmd
pip install pyinstaller
pyinstaller --onefile --add-data "index.html;." --windowed bruno_blockchain_real.py
```
O arquivo unificado estará disponível no diretório `dist/bruno_blockchain_real.exe`.
