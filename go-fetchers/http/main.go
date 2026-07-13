// go-http-fetcher: a fast, stealthy HTTP fetch tier for LakeStream.
//
// Exposes POST /fetch returning a JSON payload that maps 1:1 to the Python
// FetchResult model. No JavaScript rendering — this is the cheap/fast tier;
// the pipeline escalates to a browser tier when this one is blocked.
package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const minHTMLBytes = 20

// A small rotation of realistic desktop user agents.
var userAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

var captchaMarkers = []string{
	"g-recaptcha", "h-captcha", "hcaptcha", "cf-challenge", "cf-turnstile",
	"turnstile", "datadome", "perimeterx", "px-captcha", "are you a robot",
}

type fetchOptions struct {
	TimeoutMs         int               `json:"timeout_ms"`
	Headers           map[string]string `json:"headers"`
	ProxyURL          string            `json:"proxy_url"`
	UserAgent         string            `json:"user_agent"`
	CaptureScreenshot bool              `json:"capture_screenshot"`
}

type fetchRequest struct {
	URL     string       `json:"url"`
	Options fetchOptions `json:"options"`
}

type fetchResponse struct {
	URL              string            `json:"url"`
	StatusCode       int               `json:"status_code"`
	HTML             string            `json:"html"`
	Headers          map[string]string `json:"headers"`
	DurationMs       int               `json:"duration_ms"`
	Blocked          bool              `json:"blocked"`
	CaptchaDetected  bool              `json:"captcha_detected"`
	ContentType      string            `json:"content_type"`
	Error            string            `json:"error,omitempty"`
}

func pickUA(reqCounter *int) string {
	ua := userAgents[*reqCounter%len(userAgents)]
	*reqCounter++
	return ua
}

func stealthHeaders(req *http.Request, ua string) {
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")
	req.Header.Set("Accept-Encoding", "gzip, deflate, br")
	req.Header.Set("sec-ch-ua", `"Chromium";v="125", "Not.A/Brand";v="24"`)
	req.Header.Set("sec-ch-ua-mobile", "?0")
	req.Header.Set("sec-ch-ua-platform", `"Windows"`)
	req.Header.Set("Sec-Fetch-Dest", "document")
	req.Header.Set("Sec-Fetch-Mode", "navigate")
	req.Header.Set("Sec-Fetch-Site", "none")
	req.Header.Set("Upgrade-Insecure-Requests", "1")
}

func detectCaptcha(html string) bool {
	lower := strings.ToLower(html)
	for _, m := range captchaMarkers {
		if strings.Contains(lower, m) {
			return true
		}
	}
	return false
}

var reqCounter int

func handleFetch(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	var fr fetchRequest
	if err := json.NewDecoder(r.Body).Decode(&fr); err != nil || fr.URL == "" {
		writeJSON(w, fetchResponse{Blocked: true, Error: "invalid request"})
		return
	}

	timeout := 30 * time.Second
	if fr.Options.TimeoutMs > 0 {
		timeout = time.Duration(fr.Options.TimeoutMs) * time.Millisecond
	}

	transport := &http.Transport{}
	if fr.Options.ProxyURL != "" {
		if p, err := url.Parse(fr.Options.ProxyURL); err == nil {
			transport.Proxy = http.ProxyURL(p)
		}
	}
	client := &http.Client{Timeout: timeout, Transport: transport}

	req, err := http.NewRequest("GET", fr.URL, nil)
	if err != nil {
		writeJSON(w, fetchResponse{URL: fr.URL, Blocked: true, Error: err.Error(),
			DurationMs: msSince(start)})
		return
	}
	ua := fr.Options.UserAgent
	if ua == "" {
		ua = pickUA(&reqCounter)
	}
	stealthHeaders(req, ua)
	for k, v := range fr.Options.Headers {
		req.Header.Set(k, v)
	}

	resp, err := client.Do(req)
	if err != nil {
		writeJSON(w, fetchResponse{URL: fr.URL, Blocked: true, Error: err.Error(),
			DurationMs: msSince(start)})
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(io.LimitReader(resp.Body, 10<<20)) // 10 MB cap
	html := string(body)

	headers := map[string]string{}
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	blocked := resp.StatusCode == 403 || resp.StatusCode == 429 || resp.StatusCode == 503 ||
		len(html) < minHTMLBytes
	writeJSON(w, fetchResponse{
		URL:             fr.URL,
		StatusCode:      resp.StatusCode,
		HTML:            html,
		Headers:         headers,
		DurationMs:      msSince(start),
		Blocked:         blocked,
		CaptchaDetected: detectCaptcha(html),
		ContentType:     resp.Header.Get("Content-Type"),
	})
}

func msSince(t time.Time) int { return int(time.Since(t).Milliseconds()) }

func writeJSON(w http.ResponseWriter, v fetchResponse) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func main() {
	http.HandleFunc("/fetch", handleFetch)
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok","tier":"go_http"}`))
	})
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Printf("go-http-fetcher listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
