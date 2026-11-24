# Mídias sintéticas da F07

Os arquivos foram gerados com FFmpeg 7.1 a partir de `testsrc2`, uma cor sólida e uma onda de 440 Hz. `short.mp4` e `short.mov` contêm H.264 Baseline. `short.webm` contém VP8. As três amostras duram três segundos e têm resolução de 640 × 360. Os vídeos de borda são H.264 de 32 × 32 a 1 fps e duram exatamente 60, 61, 300 ou 301 segundos.

As amostras `tone` duram aproximadamente três segundos e usam MP3 Layer III, AAC em M4A, PCM em WAV, Vorbis em OGG e Opus em WebM. O teste de duração exata do áudio monta PCM/WAV em memória. A execução da suíte usa os arquivos pré-gerados e não depende do encoder.
