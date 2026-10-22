# Fast HTTP fetch tier (no browser). Tiny static binary on alpine.
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /go-http-fetcher .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=build /go-http-fetcher /usr/local/bin/go-http-fetcher
EXPOSE 8080
CMD ["/usr/local/bin/go-http-fetcher"]
