# Uma imagem serve os dois transportes — o que muda é como você a executa.
#   stdio (IDE):  docker run -i --rm --env-file .env mcp-unified --profile ide
#   HTTP (agente): docker compose up
FROM python:3.12-slim

# Evita .pyc e força stdout/stderr sem buffer. O unbuffered importa: no stdio,
# log em buffer pode sair fora de ordem em relação ao protocolo.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Camada de dependências separada do código, para reaproveitar cache entre builds.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Usuário sem privilégio. O servidor não escreve em disco nem abre porta
# privilegiada, então não há motivo para rodar como root.
RUN useradd --create-home --shell /usr/sbin/nologin mcp
USER mcp

# Sem EXPOSE fixo: a porta só existe no modo HTTP, e é configurável.
ENTRYPOINT ["mcp-unified"]
CMD ["--transport", "stdio", "--profile", "ide"]
