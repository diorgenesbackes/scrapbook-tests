# Scrap Book — Testes não unitários

Repositório reservado às suítes HTTP, integração com PostgreSQL, navegador, segurança, carga e recuperação do Scrap Book, conforme o [HLD](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/HLD.md) e os [guidelines](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/README.md).

## Estado atual

A feature `GE-STORY-001` inclui Python 3.13.15/biblioteca padrão, probes .NET e Playwright **1.65.0**. Implementa contratos HTTP, isolamento, concorrência, falhas transacionais, jornadas de navegador e perfil de carga H-03. Não há k6 instalado nem execução automática em CI. A branch não foi promovida/publicada.

Os unitários permanecem em `scrapbook-service` e `scrapbook-ui`. O projeto Liquibase mantém suas próprias validações de migrations.

## Execução e evidências

Com os repositórios clonados como diretórios irmãos e o PostgreSQL local iniciado:

```bash
npm ci
npx playwright install chromium webkit
dotnet restore probes/ScrapBook.Probes.csproj --locked-mode
python3 run.py preflight
python3 run.py foundation
python3 run.py keys
```

`foundation` verifica o papel DML, tentativas de DDL negadas, evento/membro/auditoria em transação desfeita, repetição/reversão do bootstrap e colisão de conta sintética. Preserva as 33 tabelas/47 migrations e os dados fora da massa do teste. `keys` recria o provider com o mesmo diretório privado e confere a leitura do cursor e permissões. O runner rejeita destinos HTTP fora de loopback antes de abrir conexão.

Os comandos precisam de Docker e SDK .NET 10/Node 26.13.0/npm 11.20.0. Playwright tem perfis Chromium desktop e WebKit móvel emulado, sem equivalência a validação em aparelhos físicos. Relatórios, traces e navegadores instalados não entram no Git.

As suítes são manuais; não são executadas em merge, deploy ou startup da aplicação.

## Jornadas de Events em banco descartável

Restaurar/compilar `scrapbook-service` e executar `npm run build` em `scrapbook-ui` antes. Instalar os requisitos acima, Liquibase 4.33.0/Java e iniciar Docker local. A partir deste repositório:

```bash
python3 journeys.py models
python3 journeys.py backend
python3 journeys.py browser
python3 journeys.py load
python3 journeys.py all
```

`event_lab.py` exige socket Unix local, cria container/portas loopback exclusivos e aplica as mesmas 47 migrations com migrador sem superusuário. Prepara runtime DML e três contas sintéticas. APIs usam contas fixas por configuração; nenhuma credencial vai para argumentos, manifesto de navegador ou relatório. O container descartável e processos são encerrados ao final; o Compose/volume persistente do usuário não é alterado.

- `backend`: contratos, paginação, conta alheia/segundo responsável, rollback antecipado/diferível, gatilhos, transições e obrigações antigas; depois indisponibilidade/lock, orçamento compartilhado e privacidade.
- `browser`: contratos HTTP + 14 jornadas em Chromium desktop e WebKit/iPhone emulado, com fusos Tokyo/Los Angeles, build de produção servido por preview e proxy para a API do laboratório.
- `load`: completa a massa em 200 eventos, aquece três ciclos por worker e executa 10 concorrentes por 60s. Cada worker atualiza evento próprio com versão válida; mede lista/detalhe/update separadamente, exige zero erros e p95 ≤500ms.
- `all`: executa todas essas etapas na mesma base exclusiva; relatório sanitizado em `artifacts/all-report.json`. Saídas brutas e capturas ficam ignoradas; evidência revisada é versionada em scrapbook-docs.

As falhas injetadas, funções/triggers temporários e snapshot estrutural travado existem somente nesse laboratório. Não representam implementação de Records/consumidor ou convites. O cleanup elimina o container exclusivo inteiro, sem tentar apagar eventos/auditoria em uma base real. Interromper o runner permite o bloco finally executar; após kill abrupto, conferir/remover somente o container `scrapbook-events-test-<identificador>` da execução conhecida.

O teste temporal usa relógio real do PostgreSQL com eventos já encerrados e valida janela 12h/13h; os instantes exatos antes/no/depois do meio-dia são cobertos por unitários substituíveis, sem afirmar execução de integração durante o minuto real de 12h. Emulação não substitui aparelhos físicos, leitor de tela ou auditoria WCAG completa. H-03 não mede os 200 uploads do PRD nem infraestrutura AWS.

Usar fixtures sintéticas, destino explicitamente validado e limpeza restrita à própria massa. Registrar ambiente, revisão, comandos, resultado, falhas e testes não executados nos ciclos de `scrapbook-docs/testing/`, quando existirem.

## Dependências e dados locais

Versionar somente código de testes, configuração sem segredos, fixtures seguras, manifests e lockfiles. Instalar dependências pelas ferramentas escolhidas; não adicionar bibliotecas instaladas, ambientes virtuais, binários, relatórios brutos, credenciais ou conteúdo real de convidados.

O `.gitignore` cobre dependências e saídas comuns de Node, Python e .NET para a futura implementação, sem afirmar que essas ferramentas já foram adotadas.

## Contribuição

A branch principal é `main`. Seguir o [guia de contribuição](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/AGENTS.md) e o [Git Flow](https://github.com/diorgenesbackes/scrapbook-docs/blob/main/guidelines/git-flow.md). Versionar manifests e lockfiles; dependências instaladas, artefatos gerados e segredos permanecem locais.

## Consumidores após RF-STORY-001

Os probes usam os namespaces por conceito e o ScrapBookDbContext único. O inventário esperado passa a cinco mapeamentos (quatro Events e conta Identity); autorização é conferida por IEventRepository. `journeys.py models` executa o probe em banco descartável, sem precisar iniciar o Compose do usuário. backend/all também incluem esse probe. A suíte verifica os mesmos contratos após a migração para Controllers MVC. No complemento de isolamento, Models/Enums/Builders/Requests/Responses e entidades POCO passam a ScrapBook.Domain; probes usam Domain.Entities. DbContext/configurações EF continuam em Infrastructure. Services são exclusivos de Application, e os adaptadores técnicos permanecem nas camadas externas.

## Stack inteira em containers

Após construir as imagens com `python3 scripts/stack_local.py build` em infrastructure, executar manualmente `python3 docker_stack.py` neste repositório. Requer Docker Compose, Liquibase/Java e Playwright instalados conforme os requisitos acima.

A suíte usa o compose.yaml real com projeto aleatório, credenciais/portas/rede/volumes próprios, aplica as mesmas 47 migrations e prepara runtime DML/fixture sintética. Confere identidade desativada por padrão, DNS interno, API/UI sem root e filesystem somente leitura, ausência de SDK/Node no runtime, health direto/proxy, SPA/assets, JSON/versão/auditoria, dados e cursor após down/up, 14 jornadas e H-03 através de Nginx→API→PostgreSQL. Não usa o Compose/volume persistente do usuário.

Relatório sanitizado: artifacts/docker-stack-report.json. Diagnóstico privado de comando que falhou: artifacts/docker-stack-private.log; logs recentes dos containers: artifacts/docker-stack-containers-private.log. Nunca versionar esses diagnósticos. A carga imprime métricas agregadas antes de aplicar os critérios de aceite, inclusive quando falhar. Cleanup remove somente os volumes do projeto scrapbook-docker-test-<identificador>. O uso de down --volumes pertence exclusivamente a esse laboratório; a parada habitual preserva volumes.

## Catálogo de identidade visual

Execução manual: `npx playwright test --config visual-identity.config.ts`. O runner inicia/encerra um servidor HTTP exclusivo em loopback:4175 que serve somente scrapbook-docs/visual-identity. Não usa API, banco ou stack persistente.

Cobre inventário FR-001–FR-022, 38 telas/178 estados em 320/1440px e Chromium/WebKit, layouts representativos em 320/390/768/1440px, proporções de mídia, assets, teclado/foco/abas/diálogo, proteção de bloqueio/recibo, alteração de nome/concordância e contadores. Inclui texto 200% e reduced motion. Relatório JSON e capturas em artifacts/visual-identity*, ignorados pelo Git; evidências selecionadas/revisadas ficam na documentação.

A suíte não comprova acessibilidade integral, leitor de tela, aparelhos físicos, usabilidade, Cognito ou captura/upload/reprodução reais. A proposta visual depende da revisão do responsável antes do aceite.

## Página pública do evento (PP-STORY-001)

Execução manual: `python3 public_event.py`, após build das imagens com `python3 scripts/stack_local.py build` em infrastructure. Usa o Compose real com banco/portas/volumes exclusivos, mesmas 47 migrations, 1.000 eventos sintéticos e identidade desabilitada em Production durante os acessos públicos.

Cobre projeção mínima, códigos inválidos/PK/caixa/padding, correlação, autorização privada, ausência de gravações nas tabelas de aplicação, uso do índice existente, OpenAPI, proxy e fallback SPA. Playwright inclui acesso direto, reload, nova aba, teclado/foco e layout em Chromium e WebKit emulado. Carga: 10 concorrentes por 60s, zero erros e p95 ≤500ms; não mede upload ou AWS.

Relatórios e capturas ignorados em `artifacts/public-event*`; evidência revisada fica em scrapbook-docs. Cleanup remove somente o projeto/volumes descartáveis desta execução. Não rodar contra banco compartilhado ou volume persistente. A configuração `public_event.config.ts` pode ser chamada separadamente somente com URLs loopback e manifesto privado preparados pelo runner.

## Apresentação pública (PP-STORY-002)

Executar manualmente `python3 public_presentation.py` após construir imagens em infrastructure. O laboratório Compose usa seus próprios recursos, aplica as mesmas 47 migrations e prepara 1000 eventos de carga mais quatro fixtures de apresentação. Verifica edição/restauração da mensagem via gestão, projeção pública exata, autorização, leitura sem escrita, capa pública, markup seguro, Unicode, layout, árvore acessível e revalidação.

Playwright `public_presentation.config.ts`: Chromium e WebKit emulados, mais Chromium com cinco amostras frias de página pronta em perfil 4G declarado (10/2Mbps, 80ms, CPU 4×, 390×844, cache desligado, limite 3s). Carga separada de API: dez clientes por 60s. Inclui 14 cenários de regressão da gestão. Relatórios/capturas em artifacts/public-presentation*, ignorados; evidências revisadas na documentação.

`public_event.py` evolui suas asserções para os campos aditivos da apresentação; o código histórico de PP-STORY-001 continua acessível pelas revisões daquela entrega. Snapshots de todas as tabelas de aplicação preservam o contrato somente leitura. Não há teste com leitor de tela/aparelho físico ou AWS; árvore acessível é uma verificação semântica parcial.

## Compartilhamento/QR (PP-STORY-003)

Após npm ci e build das imagens, executar python3 event_sharing.py. Laboratório exclusivo valida dois responsáveis, outsider, código/PK, Host adulterado, origem HTTPS externa sintética e origem ausente. Não acessa o domínio sintético. Comparação de digests confirma leitura sem escrita. Configuração temporária é restrita aos containers próprios.

Playwright em Chromium/WebKit: QR público, navegação anônima, PNG, clipboard manual, impressão em mídia emulada e exportação inválida. jsQR 1.4.0 (Apache-2.0) + pngjs 7.0.0 (MIT) leem independentemente o QR gerado por qrcode: preview, screenshot, PNG exportado e composição print. Artefatos em artifacts/event-sharing* são ignorados; evidências revisadas ficam nos docs. Papel/aparelhos reais não são simulados nem declarados aprovados por essas verificações.

## Janela de novos envios (PP-STORY-004)

Construir as imagens em infrastructure, compilar `dotnet build probes/ScrapBook.Probes.csproj --no-restore --disable-build-servers` e executar manualmente `python3 submission_window.py`. O laboratório Compose cria suas próprias portas/credenciais/volumes e aplica as mesmas 47 migrations. Não usa a stack persistente.

O modo `public-window` do probe inicia Kestrel loopback com os controllers/middleware/repositório/serviço reais e o mesmo PostgreSQL DML, substituindo somente TimeProvider por leitura de arquivo privado de teste. Não usa o Program/composição completo da API de produção; o Compose real verifica esse host separadamente. Nenhum endpoint público altera hora e nenhuma opção de relógio é adicionada à aplicação. O teste cria e remove processo/arquivo exclusivos.

Matriz HTTP cobre antes/no/depois de abertura/12h, 12h30/13h/depois, madrugada, viradas de mês/ano/bissexto e galeria independente. Browser encaminha somente o GET público selecionado ao host temporal; demais recursos usam Nginx real. Chromium Tokyo/WebKit Los Angeles têm Date fixada em 2099, mantendo timers monotônicos reais. Conferem transições em página aberta, QR preservado, falha/retorno, resposta antiga e edição de data pela API real. Outra jornada usa exclusivamente Compose, para parar e reiniciar o container service do próprio projeto e comparar prazos. Não altera relógio do SO, serviço persistente ou ambiente externo.

Relatórios/capturas em artifacts/submission-window* são ignorados, com cópia revisada em docs. Comparações de regressão validam os campos dinâmicos a partir de serverTime, não igualdade integral entre respostas de instantes distintos. A medição 4G também aguarda disponibilidade e QR. Sem sessão de upload, aparelho físico ou leitor de tela real declarado.

## Recuperação pública (PP-STORY-005)

Após build das imagens em infrastructure, executar manualmente `python3 public_recovery.py`. Usa Compose real, role DML e as mesmas 47 migrations, exclusivamente em banco/portas/volumes aleatórios. Confere códigos vazios/malformados/longos/encoding/ausentes, correlação, formato dos erros e snapshots somente leitura. Revoga/restaura SELECT apenas do runtime descartável para provocar 500, desabilita/restaura login para 503 e mantém um lock exclusivo para timeout. Finally restaura permissões/role e encerra o locker antes do cleanup do projeto.

Chromium/WebKit cobrem links guest inválidos, rede/503 HTML/429, cooldown sem retry automático, timeout de 6s, resposta tardia, duplo clique, foco e retry pelo teclado, banco indisponível com recuperação sem reload e janela encerrada atual, capa/QR isolados e consulta privada de compartilhamento. Mutação/restauração de data usa a API de gestão e grava fixture/auditoria intencionalmente. Transporte pode recusar URI antes da aplicação; documentar o status observado sem prometer Problem Details do edge.

Relatórios/capturas em artifacts/public-recovery* são ignorados; evidências revisadas ficam nos docs. Não rodar essas injeções em dados compartilhados ou no Compose persistente. Não há SLA/AWS/aparelho físico/leitor de tela real declarado.
