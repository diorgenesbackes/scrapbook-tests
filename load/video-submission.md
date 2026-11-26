# Carga local de retomada de vídeo

Execute na raiz de `scrapbook-tests`, junto aos checkouts correspondentes de serviço, UI, Liquibase e infraestrutura. O runner usa as dependências da [stack de vídeo](../video_submission_stack.py), Docker, AWS CLI e k6 no PATH. Informe um diretório novo para as evidências.

```sh
python3 -B video_submission_load.py --output /tmp/scrapbook-video-load
```

O primeiro perfil valida a jornada com um vídeo. Com o aquecimento concluído, quatro usuários virtuais executam duas tentativas novas cada, distribuídas entre dois eventos. A fixture é um MP4 H.264 sintético válido de 48 MiB, com preenchimento em caixa `free`. Cada tentativa envia a primeira parte de 16 MiB, pausa por um segundo e consulta as partes reconhecidas antes de enviar as duas restantes. O teto de transferência é 48 MiB no aquecimento e 384 MiB na medição. O runner verifica a confirmação em até 60 s após solicitar a conclusão.

A medição de admissão é independente. Usa 40 usuários virtuais e cinco tentativas únicas por usuário, com 100 admissões por evento. O perfil termina na resposta de admissão, sem abrir multipart nem transferir vídeo. Cada cenário tem um limite de dois minutos. Falhas de contrato ou requisição abortam o cenário, e os resultados dos perfis são propagados ao código de saída.

Os resultados incluem p95 de 500 ms por operação de API exercitada, amostras separadas dos PUTs, bytes apresentados e aceitos, bytes reconhecidos na retomada, retransmissão e confirmações. O tempo até o recibo inclui um observador local que consulta as versões criadas no S3 descartável, publica as notificações sintéticas no SQS e permite que o serviço confirme a mídia. A consulta ao status é feita a cada segundo até observar o recibo. As amostras de CPU e memória vêm de `docker stats`.

Os volumes locais permitem verificar o contrato de retomada sob carga delimitada. A meta de 200 envios simultâneos com mistura de mídias e a disponibilização das variantes permanecem para o ensaio do perfil completo do PRD. O runner encerra sua própria stack descartável, e os resultados ficam no diretório informado com acesso restrito.
