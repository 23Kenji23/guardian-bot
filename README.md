# 🛡️ Guardian

**Um bot de monitoramento simples que roda no seu próprio celular, PC ou servidor.** Ele vigia seus sites e portas e avisa você — no Telegram, Discord, Slack, ntfy ou e-mail — quando algo cai e quando volta.

[English version → README.en.md](README.en.md)

- **Um arquivo só** (`guardian.py`), **sem instalar nada**: usa apenas a biblioteca padrão do Python 3.11+.
- **Roda em qualquer lugar que tenha Python 3.11+**: Linux, macOS, Windows, Raspberry Pi, Termux no Android. (Os testes automáticos rodam em Linux; nos outros sistemas o código é o mesmo, só biblioteca padrão, mas não foi testado lá.)
- **Seus dados ficam com você**: não existe conta, nuvem ou servidor do Guardian. Ele só fala com o que *você* configurar.
- **Sem alarme falso**: só avisa "fora do ar" depois de falhas seguidas (você escolhe quantas) e avisa uma vez só, não a cada minuto.
- Licença **MIT**: use, copie, modifique, venda, faça o que quiser.

## Para que serve

Você tem um site, uma loja, uma API ou um servidor em casa e quer **saber que caiu antes do seu cliente avisar**. O Guardian faz isso e nada além: verifica de tempos em tempos e manda uma mensagem.

| Ele faz | Ele não faz |
|---|---|
| Verifica sites (HTTP/HTTPS) e portas (TCP) | Gráficos e histórico longo (guarda só os últimos 200 eventos) |
| Confere texto na página e código de resposta | Monitoramento de dentro do servidor (CPU, disco…) |
| Avisa quando o certificado SSL está perto de vencer | Avisar *sozinho* se o próprio aparelho desligar (isso precisa de um serviço de fora: veja "Quem vigia o vigia") |
| **Vigia seus scripts e `cron`**: avisa se o "sinal de vida" deixar de chegar | Painel web com login e usuários |
| **Controle pelo Telegram**: `/pausar`, `/retomar`, `/verificar` (manutenção sem alarme) | |
| **Sinal de vida externo**: um serviço de fora avisa se o Guardian ou o aparelho morrer | |
| API local e página de status (somente leitura) | |

## Começando em 5 minutos

```sh
git clone https://github.com/23Kenji23/guardian-bot.git
cd guardian-bot
python3 guardian.py init          # cria o guardian.toml de exemplo
# edite o guardian.toml: troque o site de exemplo pelo(s) seu(s)
python3 guardian.py check         # testa agora, uma vez (sai com código 1 se algo estiver fora)
python3 guardian.py run           # vigia continuamente
```

Ou, sem Git: baixe só o `guardian.py`, coloque numa pasta e rode `python3 guardian.py init`.

O mínimo que você precisa no `guardian.toml`:

```toml
[[check]]
name = "Meu site"
url = "https://meusite.com.br"
```

Sem nenhum canal de aviso ligado, o Guardian escreve os avisos na tela. Para receber no celular, ligue um canal abaixo.

## Comandos

| Comando | O que faz |
|---|---|
| `python3 guardian.py init` | Cria um `guardian.toml` de exemplo (não sobrescreve o que já existe) |
| `python3 guardian.py check` | Verifica tudo uma vez e mostra o resultado. Saída `0` = tudo no ar, `1` = algo fora (ótimo para `cron`) |
| `python3 guardian.py run` | Vigia continuamente e avisa |
| `python3 guardian.py test-notify` | Manda uma mensagem de teste para os canais ligados |
| `-c outro.toml` | Usa outro arquivo de configuração (ou defina `GUARDIAN_CONFIG`) |

## O que vigiar

```toml
[[check]]
name = "Loja"
type = "http"                    # padrão
url = "https://loja.exemplo.com"
expect_status = [200]            # padrão: qualquer 200–399
contains = "Carrinho"            # o texto precisa aparecer na página (pega site "no ar" mas quebrado)
interval = 30                    # sobrescreve o intervalo padrão
cert_warn_days = 14              # avisa 14 dias antes do certificado SSL vencer (0 = desligado)

[[check]]
name = "Servidor SSH"
type = "tcp"
host = "meu-servidor.exemplo.com"
port = 22

[[check]]
name = "Backup"
type = "push"                    # quem dá o sinal é o SEU script (veja "Vigiar scripts e cron")
interval = 86400                 # de quanto em quanto tempo o sinal deve chegar (segundos)
grace = 3600                     # folga antes de avisar (segundos; padrão 60)
```

Configurações gerais, na seção `[guardian]`:

| Chave | Padrão | Significado |
|---|---|---|
| `language` | `"pt"` | Idioma das mensagens: `"pt"` ou `"en"` |
| `interval` | `60` | Segundos entre verificações (mínimo 5) |
| `timeout` | `10` | Segundos de espera por resposta |
| `failures_to_alert` | `2` | Falhas seguidas antes de avisar. `1` avisa na hora; `3` é mais calmo |
| `remind_minutes` | `0` | Repete o aviso a cada N minutos enquanto continuar fora (`0` = não repete) |
| `state_file` | `guardian-state.json` | Onde guarda a situação, para não repetir avisos quando você reinicia |

## Onde receber os avisos

**Segredos (token, senha) vão em variáveis de ambiente**, não no arquivo: assim você pode publicar ou compartilhar o `guardian.toml` sem vazar nada.

### Telegram (recomendado: grátis e rápido)

1. No Telegram, converse com **@BotFather**, envie `/newbot` e siga os passos. Ele entrega um **token**.
2. Abra uma conversa com o seu bot novo e mande qualquer mensagem (ex.: "oi").
3. Descubra o seu **chat_id**: rode `curl "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates"` e procure `"chat":{"id":123456789`. Esse número é o chat_id.
4. Configure:
   ```toml
   [notify.telegram]
   enabled = true
   token_env = "GUARDIAN_TELEGRAM_TOKEN"
   chat_id = "123456789"
   ```
5. Defina o token e teste:
   ```sh
   export GUARDIAN_TELEGRAM_TOKEN="123456:ABC..."     # Windows (PowerShell): $env:GUARDIAN_TELEGRAM_TOKEN="..."
   python3 guardian.py test-notify
   ```

No Telegram o bot também **responde comandos** (veja "Controlar pelo Telegram"). Ele só obedece ao seu `chat_id`; qualquer outra pessoa que achar o bot é ignorada. Atenção: se o `chat_id` for de um **grupo**, qualquer pessoa do grupo manda no bot.

### Controlar pelo Telegram

| Comando | O que faz |
|---|---|
| `/status` | Situação de tudo (e as pausas ativas) |
| `/verificar` ou `/verificar agora` | Verifica tudo agora e mostra o resultado. `/verificar Meu site` verifica só um item |
| `/pausar` | Silencia **todos** os avisos por 1 hora |
| `/pausar 30m` · `/pausar 2h` · `/pausar 1d` · `/pausar 1h30m` | Silencia pelo tempo dado (de 1 minuto a 7 dias). Número sem letra = minutos |
| `/pausar 2h Meu site` | Silencia só aquele item (o nome pode ter espaços e não diferencia maiúsculas) |
| `/retomar` · `/retomar Meu site` | Encerra a pausa antes da hora |
| `/ajuda` | Lista os comandos (`/manutencao` é outro nome de `/pausar`; os nomes em inglês também funcionam: `/pause`, `/resume`, `/check`) |

**Como a pausa funciona (para você derrubar um site de propósito sem ser incomodado):**

- O Guardian **continua verificando** durante a pausa: `/status` e a página de status mostram a verdade. Só os **avisos** ficam calados.
- Quando a pausa acaba (ou você usa `/retomar`), se algo **continua fora do ar**, você é avisado na hora ("X continua fora do ar há …"). Se voltou sozinho durante a pausa, não há mais nada a dizer.
- Se você **já tinha sido avisado** de que algo caiu e ele volta durante a pausa, o aviso de "voltou" chega mesmo assim (senão a queda ficaria sem desfecho).
- A pausa é gravada em disco: reiniciar o Guardian não a cancela. Se ela terminar enquanto o Guardian estava desligado, o aviso de fim sai quando ele ligar.
- Esses comandos só mexem nos **avisos**. Não executam nada e não alteram o `guardian.toml`.

### Webhook (Discord, Slack, ntfy ou genérico)

```toml
[notify.webhook]
enabled = true
url_env = "GUARDIAN_WEBHOOK_URL"     # a URL do webhook vai nesta variável de ambiente
format = "discord"                   # "discord" | "slack" | "ntfy" | "generic"
```

`generic` envia um JSON com `text`, `kind` (`down`, `up`, `still_down`, `cert`), `check` e `time`, para você ligar em qualquer outra coisa.

### E-mail

```toml
[notify.email]
enabled = true
host = "smtp.exemplo.com"
port = 587
security = "starttls"        # "starttls" | "ssl" | "none" (só para servidor local)
username = "voce@exemplo.com"
password_env = "GUARDIAN_SMTP_PASSWORD"
from = "voce@exemplo.com"
to = ["voce@exemplo.com"]
```

Se um canal falhar, o Guardian tenta 3 vezes e registra o erro, sem travar as verificações nem os outros canais.

## Quem vigia o vigia

O Guardian mede a partir de onde está. Se o celular desligar, a bateria acabar ou a internet cair, **ele não tem como avisar que parou**. Quem avisa tem que estar *de fora*. Por isso o Guardian faz o contrário: ele **dá sinal de vida** a um serviço externo, e é o serviço que te alerta quando o sinal para.

```toml
[heartbeat]
enabled = true
url_env = "GUARDIAN_HEARTBEAT_URL"   # a URL de sinal do serviço (é um segredo: fica na variável de ambiente)
interval = 60                        # segundos entre sinais (10 a 3600)
```

1. Crie um monitor do tipo "push" / "sinal de vida" num serviço externo (por exemplo o **healthchecks.io**, que tem plano grátis, ou o modo *push* do **Uptime Kuma** hospedado em outro lugar) e configure o alerta dele para o seu e-mail/Telegram. Ajuste a tolerância do serviço para uns 2 a 3 intervalos.
2. Copie a URL de sinal que ele dá e ponha na variável de ambiente `GUARDIAN_HEARTBEAT_URL`.
3. Teste com `python3 guardian.py test-notify` (ele manda um sinal e mostra `OK heartbeat`).

O Guardian faz um `GET` simples nessa URL. **Só dá o sinal enquanto o seu laço principal está andando**: se ele travar (e não só morrer), o sinal para também. Desligou, ficou sem internet, travou ou foi encerrado → o serviço externo para de receber e te avisa.

> Testei o sinal contra um servidor de mentira que registra os `GET`s, não contra o healthchecks.io nem o Uptime Kuma de verdade. O formato (um `GET` na URL) é o que esses serviços usam, mas confira no teste deles.

## Vigiar scripts e `cron`

Um backup que parou de rodar não "cai": ele só **deixa de dar sinal**. Para isso existe o check `push`. Quem avisa que está vivo é o seu script, e o Guardian alerta quando o sinal deixa de chegar.

```toml
[api]
enabled = true
token_env = "GUARDIAN_API_TOKEN"     # obrigatório para checks push

[[check]]
name = "Backup"
type = "push"
interval = 86400                     # o sinal deve chegar a cada 24 h...
grace = 3600                         # ...com 1 h de folga
```

No fim do seu script (só se tudo deu certo) ou no `cron`:

```sh
curl -fsS -m 10 -X POST -H "Authorization: Bearer $GUARDIAN_API_TOKEN" http://127.0.0.1:8765/ping/Backup
# se o script souber que falhou, avise na hora em vez de esperar o prazo:
curl -fsS -m 10 -H "Authorization: Bearer $GUARDIAN_API_TOKEN" http://127.0.0.1:8765/ping/Backup/fail
```

- Sinal que atrasa além de `interval + grace` → aviso "nenhum sinal há …". Um atraso já é prova suficiente: **não espera uma segunda falha**, nem para `/fail`.
- O sinal seguinte → aviso de "voltou ao ar". Nomes com espaço viram `%20` na URL (`/ping/Meu%20Backup`); prefira nomes sem espaço.
- Recém-ligado, o Guardian dá uma janela cheia antes de cobrar sinal, então reiniciar não gera alarme falso.
- É a **única exceção** ao "somente leitura" da API: só existe com token, só aceita `GET`/`POST` em `/ping/<item>` (nunca `HEAD`), só vale para itens `push` e só atualiza o relógio daquele item. Veja [SECURITY.md](SECURITY.md).
- `guardian.py check` (a verificação avulsa) não consegue conferir itens `push`: ele os mostra como `SKIP`.

## API local e página de status (opcional)

```toml
[api]
enabled = true
host = "127.0.0.1"     # só este aparelho
port = 8765
```

| Endereço | O que devolve |
|---|---|
| `/` | Página de status (abre no navegador, atualiza sozinha) |
| `/api/v1/status` | Resumo + situação de cada item (JSON) |
| `/api/v1/checks` | Só a lista de itens |
| `/api/v1/events` | Últimos avisos (mais recente primeiro) |
| `/health` | `{"status":"ok"}` — para saber se o próprio Guardian está vivo |
| `/ping/<item>` e `/ping/<item>/fail` | Só para itens `push` (veja "Vigiar scripts e cron"). **Exige token sempre** |

É **somente leitura** (qualquer `POST`, `PUT`… responde 405) e **não mostra usuário, senha nem parâmetros das suas URLs**. A única exceção é o `/ping/` acima. Para abrir a API para a rede, troque `host` por `"0.0.0.0"` **e** defina `token_env`: sem token o Guardian se recusa a iniciar. Com token, use `Authorization: Bearer <token>` (ou, no navegador, qualquer usuário e o token como senha).

## Deixar rodando o tempo todo

**Linux (systemd, sem root):** crie `~/.config/systemd/user/guardian.service`:

```ini
[Unit]
Description=Guardian
After=network-online.target

[Service]
WorkingDirectory=%h/guardian
EnvironmentFile=%h/guardian/guardian.env
ExecStart=/usr/bin/python3 guardian.py run
Restart=on-failure

[Install]
WantedBy=default.target
```

Coloque `GUARDIAN_TELEGRAM_TOKEN=...` no `guardian.env` (`chmod 600 guardian.env`) e rode `systemctl --user enable --now guardian`.

**Android (Termux):** `pkg install python`, siga os passos acima, e rode dentro do `tmux` ou crie `~/.termux/boot/guardian.sh` (com o app **Termux:Boot**):

```sh
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/guardian && python guardian.py run >> guardian.log 2>&1
```

> O Android pode encerrar o Termux para economizar bateria. Desative a otimização de bateria do Termux. **Ainda não foi testado num Android real**; o teste automático roda em Linux.

**Windows:** `py guardian.py run` num atalho, ou no Agendador de Tarefas ("ao iniciar o computador"). Defina o token com `setx GUARDIAN_TELEGRAM_TOKEN "..."`.

**Sem rodar o tempo todo:** use `check` no `cron` e receba só quando falhar — por exemplo `*/5 * * * * cd ~/guardian && python3 guardian.py check || echo "algo caiu" | mail ...`. Neste modo o Guardian não manda avisos sozinho nem lembra do estado; ele só devolve o código de saída.

## Segurança em resumo

- **Nenhum texto recebido vira comando.** O Guardian não executa programas nem usa shell. As únicas entradas são o seu `guardian.toml` e, no Telegram, os comandos (`/status`, `/verificar`, `/pausar`, `/retomar`, `/ajuda`) vindos do *seu* chat. Eles só mexem nos avisos.
- Segredos (token, senha, URL do sinal de vida) ficam em variáveis de ambiente e são trocados por `***` se aparecerem no registro. O arquivo de estado é gravado com permissão `0600`.
- A API é somente leitura (exceto `/ping/`, que exige token), escuta em `127.0.0.1` por padrão e esconde usuário/senha/parâmetros das URLs.
- Redirecionamentos só seguem para `http(s)://`; `file://` é recusado.
- Detalhes e como reportar um problema: [SECURITY.md](SECURITY.md).

## Limites (leia antes de depender dele)

- **Se o aparelho onde o Guardian roda desligar ou ficar sem internet, o Guardian em si não consegue avisar.** Quem avisa, nesse caso, é o serviço externo do `[heartbeat]` (veja "Quem vigia o vigia"). **Sem o `[heartbeat]` configurado, nenhum aviso chega.** Além disso, ele mede a partir de onde está: uma queda *dessa* rede parece uma queda *do seu site*.
- Sem gráficos, sem histórico longo, sem multiusuário. É de propósito: a ideia é ser pequeno e fácil de entender (leia o `guardian.py` inteiro em meia hora).
- Os intervalos são "melhor esforço": se o aparelho estiver sobrecarregado, uma verificação pode atrasar alguns segundos.

## Testes

```sh
python3 -m unittest discover -s tests -v
```

76 testes, todos locais (servidores falsos de HTTP, Telegram, webhook, SMTP e do serviço de sinal de vida; nenhum acessa a internet). Rodam em Python 3.11, 3.12 e 3.13 pelo GitHub Actions.

## Sobre este projeto

O Guardian é um bot pequeno e completo, publicado como está. **Não há suporte garantido nem promessa de novas funções.** Ele é MIT justamente para que você possa copiar e adaptar à vontade: faça um fork, mude o que precisar. Correções de bugs e de segurança são bem-vindas via *issue* ou *pull request*; novas funções grandes provavelmente ficam melhor no seu fork.

## Licença

[MIT](LICENSE) — livre para usar, modificar e distribuir, inclusive comercialmente, mantendo o aviso de copyright.
