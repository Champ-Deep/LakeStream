# Headless-Chrome fetch tier via chromedp. Needs a real Chromium at runtime.
FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN go build -ldflags="-s -w" -o /go-browser-fetcher .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
ENV CHROME_PATH=/usr/bin/chromium
COPY --from=build /go-browser-fetcher /usr/local/bin/go-browser-fetcher
EXPOSE 8080
CMD ["/usr/local/bin/go-browser-fetcher"]
