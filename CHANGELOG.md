# Changelog

## 1.1.0 — 2026-11
**Quem vigia o vigia** e **controle pelo Telegram**. Compatível com o 1.0: o `guardian.toml` e o arquivo de estado antigos continuam funcionando sem mudança.
- `[heartbeat]`: sinal de vida a um serviço externo (healthchecks.io, Uptime Kuma push…). Se o aparelho, a internet ou o Guardian morrerem ou travarem, o serviço de fora avisa. `test-notify` também testa o sinal.
- Checks `type = "push"` (`interval`, `grace`) para scripts e `cron`: `/ping/<item>` e `/ping/<item>/fail` na API, **sempre com token**. Atraso de sinal avisa na hora, sem esperar uma 2ª falha. É a única escrita da API; `HEAD` nunca conta.
- Telegram: `/pausar [tempo] [item]` (manutenção sem alarme, 1 min a 7 dias), `/retomar [item]`, `/verificar [agora] [item]`; nomes em inglês também (`/pause`, `/resume`, `/check`).
- A pausa é gravada em disco, continua medindo, avisa o que ainda estiver fora quando acaba e nunca deixa uma queda já avisada sem o "voltou".
- `/status`, a API e a página mostram pausas; eventos silenciados ficam no histórico (`"silent": true`).
- `check` avulso mostra itens `push` como `SKIP`.
- 76 testes (eram 34); teste com o processo real cobrindo sinal de vida, push, pausa e reinício.

## 1.0.0 — 2026-11
Primeira versão pública.
- Verificações HTTP/HTTPS (status, texto esperado, redirecionamentos seguros) e TCP.
- Aviso de certificado SSL perto de vencer.
- Avisos por Telegram, webhook (generic, Discord, Slack, ntfy) e e-mail, com 3 tentativas.
- Comandos `/status` e `/ajuda` no Telegram (só no seu chat).
- API local somente leitura e página de status (opcional, token obrigatório fora de 127.0.0.1).
- Estado salvo em disco: reiniciar não repete avisos.
- CLI: `init`, `run`, `check`, `test-notify`.
- Mensagens em português e inglês. Só biblioteca padrão, Python 3.11+.
