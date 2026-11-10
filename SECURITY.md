# Segurança / Security

## Como o Guardian foi pensado

O Guardian roda no aparelho de quem o usa e faz só isto com a rede: **sai** para verificar os destinos que *você* escreveu no `guardian.toml`, para mandar avisos e (se você ligar o `[heartbeat]`) para dar um sinal de vida à URL que *você* indicou; e, se você ligar, **escuta** uma API que é somente leitura, com uma única exceção protegida por token (`/ping/`, abaixo).

| Risco | O que o Guardian faz |
|---|---|
| Texto externo virar comando | Não usa shell nem executa programas. As únicas entradas são o `guardian.toml`, os sinais `/ping/` autenticados e os comandos `/status`, `/verificar`, `/pausar`, `/retomar` e `/ajuda` do **seu** `chat_id` no Telegram (qualquer outro chat é ignorado). Os comandos só mexem nos **avisos** (pausar, retomar, verificar agora): não executam nada e não alteram a configuração. |
| Quem pode mandar no bot | Quem estiver no `chat_id` configurado. Se for um **grupo**, qualquer membro pode pausar os avisos: prefira uma conversa privada. |
| Silenciar avisos por engano | A pausa tem limite (7 dias; `/pausar` sem tempo = 1 hora), é registrada no histórico, aparece no `/status` e, ao acabar, o que continua fora do ar é avisado na hora. |
| Segredo vazando | Token, senha, URL de webhook e URL do sinal de vida ficam em variáveis de ambiente (`*_env`). Se algum aparecer numa linha de registro, é trocado por `***`. O arquivo de estado é gravado com permissão `0600`. Um `guardian.toml` com segredo escrito e legível por outros usuários gera um aviso. |
| API exposta | Desligada por padrão. Somente leitura (outros métodos → 405). Escuta em `127.0.0.1`; fora disso o Guardian se recusa a iniciar sem token. O token é comparado em tempo constante. A API e os avisos nunca mostram usuário, senha ou parâmetros das URLs vigiadas. |
| Rota `/ping/<item>` (checks `push`) | **A única escrita da API.** Só existe se houver um check `push`, **exige token sempre** (sem token configurado a configuração é recusada, e o servidor responde 403 mesmo assim), aceita só `GET`/`POST` (`HEAD` → 405, para prefetch e proxies não darem sinal falso), só vale para itens `push`, lê no máximo 64 KB de corpo e ignora o conteúdo. O efeito é apenas atualizar o relógio daquele item. Quem tiver o token pode dar sinal falso de que um script está vivo: trate-o como senha. |
| Sinal de vida (`[heartbeat]`) | Só um `GET` para a URL que você configurou, sem enviar dados do Guardian além do `User-Agent`. Segue só redirecionamentos `http(s)`. Se o laço principal travar, o sinal para de propósito. A URL costuma carregar um segredo do serviço: por isso fica numa variável de ambiente e é mascarada no registro. |
| Página de status | Nomes e textos são escapados (sem HTML injetado); `Content-Security-Policy` restritiva, sem scripts. |
| Redirecionamentos | Só seguem para `http://` e `https://` (por exemplo, `file://` é recusado e conta como falha). |
| Alarme falso por falha momentânea | `failures_to_alert` exige falhas seguidas; cada queda é avisada uma vez. |

## O que o Guardian NÃO protege

- Quem tem acesso ao seu aparelho ou à sua conta pode ler as variáveis de ambiente e o `guardian.toml`.
- O Guardian vigia **qualquer** endereço que você escrever, inclusive de redes internas. Não aceite configuração de terceiros sem ler.
- Nada é criptografado em repouso (o arquivo de estado só tem nomes, situação, horários e pausas).
- A API local usa HTTP simples. Se você a abrir para a rede (`0.0.0.0`), o token trafega sem criptografia: ponha-a atrás de um proxy HTTPS ou de uma VPN.

## Reportar uma vulnerabilidade

Use **Security → Report a vulnerability** (relato privado) no repositório do GitHub, ou abra uma *issue* descrevendo o problema **sem** incluir tokens, senhas ou endereços privados. É um projeto sem suporte garantido, mas correções de segurança são priorizadas.

---

**EN:** Guardian never executes received text (no shell, no subprocess). Telegram commands (`/status`, `/check`, `/pause`, `/resume`, `/help`, only from your `chat_id`) affect alerts only; in a group chat every member can pause alerts. Secrets (including the heartbeat URL) live in environment variables and are masked in logs; the state file is `0600`; the optional API is read-only, loopback by default (a token is mandatory otherwise) and never reveals credentials or query strings of monitored URLs; its single write path, `/ping/<item>` for `push` checks, always requires a token, ignores request bodies, never accepts `HEAD`, and only updates that item's clock; the optional heartbeat is a plain `GET` to the URL you configure; the status page is escaped and served with a strict CSP; redirects only follow `http(s)`. The local API is plain HTTP: put it behind HTTPS or a VPN if exposed. Report vulnerabilities privately through GitHub's "Report a vulnerability".
