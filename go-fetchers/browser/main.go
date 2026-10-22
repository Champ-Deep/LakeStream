// go-browser-fetcher: a headless-Chrome fetch tier for LakeStream via chromedp.
//
// Exposes POST /fetch returning JSON that maps 1:1 to the Python FetchResult
// model, including optional full-page screenshots and scripted actions. Renders
// JavaScript and applies light stealth (navigator.webdriver removal, realistic
// UA/fingerprint) so it can handle sites the fast HTTP tier can't.
package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

const minHTMLBytes = 20

const stealthJS = `Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});`

const defaultUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

var captchaMarkers = []string{
	"g-recaptcha", "h-captcha", "hcaptcha", "cf-challenge", "cf-turnstile",
	"turnstile", "datadome", "perimeterx", "px-captcha", "are you a robot",
}

type action struct {
	Type     string `json:"type"`
	Selector string `json:"selector"`
	Ms       int    `json:"ms"`
}

type fetchOptions struct {
	TimeoutMs         int      `json:"timeout_ms"`
	ProxyURL          string   `json:"proxy_url"`
	UserAgent         string   `json:"user_agent"`
	CaptureScreenshot bool     `json:"capture_screenshot"`
	WaitForSelector   string   `json:"wait_for_selector"`
	Actions           []action `json:"actions"`
}

type fetchRequest struct {
	URL     string       `json:"url"`
	Options fetchOptions `json:"options"`
}

type fetchResponse struct {
	URL              string `json:"url"`
	StatusCode       int    `json:"status_code"`
	HTML             string `json:"html"`
	DurationMs       int    `json:"duration_ms"`
	Blocked          bool   `json:"blocked"`
	CaptchaDetected  bool   `json:"captcha_detected"`
	ContentType      string `json:"content_type"`
	ScreenshotBase64 string `json:"screenshot_base64,omitempty"`
	Error            string `json:"error,omitempty"`
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

func handleFetch(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	var fr fetchRequest
	if err := json.NewDecoder(r.Body).Decode(&fr); err != nil || fr.URL == "" {
		writeJSON(w, fetchResponse{Blocked: true, Error: "invalid request"})
		return
	}

	timeout := 45 * time.Second
	if fr.Options.TimeoutMs > 0 {
		timeout = time.Duration(fr.Options.TimeoutMs)*time.Millisecond + 15*time.Second
	}
	ua := fr.Options.UserAgent
	if ua == "" {
		ua = defaultUA
	}

	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.Flag("headless", true),
		chromedp.Flag("disable-blink-features", "AutomationControlled"),
		chromedp.NoSandbox,
		chromedp.UserAgent(ua),
	)
	if fr.Options.ProxyURL != "" {
		opts = append(opts, chromedp.ProxyServer(fr.Options.ProxyURL))
	}
	if path := os.Getenv("CHROME_PATH"); path != "" {
		opts = append(opts, chromedp.ExecPath(path))
	}

	allocCtx, cancelAlloc := chromedp.NewExecAllocator(context.Background(), opts...)
	defer cancelAlloc()
	ctx, cancel := chromedp.NewContext(allocCtx)
	defer cancel()
	ctx, cancelTimeout := context.WithTimeout(ctx, timeout)
	defer cancelTimeout()

	var html string
	var screenshot []byte

	tasks := chromedp.Tasks{
		chromedp.ActionFunc(func(ctx context.Context) error {
			return chromedp.Evaluate(stealthJS, nil).Do(ctx)
		}),
		chromedp.Navigate(fr.URL),
	}
	if fr.Options.WaitForSelector != "" {
		tasks = append(tasks, chromedp.WaitVisible(fr.Options.WaitForSelector, chromedp.ByQuery))
	} else {
		tasks = append(tasks, chromedp.Sleep(1500*time.Millisecond))
	}
	tasks = append(tasks, buildActions(fr.Options.Actions)...)
	if fr.Options.CaptureScreenshot {
		tasks = append(tasks, chromedp.FullScreenshot(&screenshot, 90))
	}
	tasks = append(tasks, chromedp.OuterHTML("html", &html, chromedp.ByQuery))

	if err := chromedp.Run(ctx, tasks); err != nil {
		writeJSON(w, fetchResponse{URL: fr.URL, Blocked: true, Error: err.Error(),
			DurationMs: msSince(start)})
		return
	}

	resp := fetchResponse{
		URL:             fr.URL,
		StatusCode:      200, // chromedp navigation succeeded
		HTML:            html,
		DurationMs:      msSince(start),
		Blocked:         len(html) < minHTMLBytes,
		CaptchaDetected: detectCaptcha(html),
		ContentType:     "text/html",
	}
	if len(screenshot) > 0 {
		resp.ScreenshotBase64 = base64.StdEncoding.EncodeToString(screenshot)
	}
	writeJSON(w, resp)
}

func buildActions(actions []action) []chromedp.Action {
	var out []chromedp.Action
	for _, a := range actions {
		switch a.Type {
		case "click":
			out = append(out, chromedp.Click(a.Selector, chromedp.ByQuery, chromedp.NodeVisible))
		case "wait_for_selector":
			out = append(out, chromedp.WaitVisible(a.Selector, chromedp.ByQuery))
		case "wait":
			ms := a.Ms
			if ms <= 0 {
				ms = 1000
			}
			if ms > 30000 {
				ms = 30000
			}
			out = append(out, chromedp.Sleep(time.Duration(ms)*time.Millisecond))
		case "scroll":
			px := a.Ms
			if px <= 0 {
				px = 2000
			}
			out = append(out, chromedp.Evaluate(scrollJS(px), nil))
		}
	}
	return out
}

func scrollJS(px int) string {
	return "window.scrollBy(0, " + itoa(px) + ")"
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
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
		_, _ = w.Write([]byte(`{"status":"ok","tier":"go_browser"}`))
	})
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Printf("go-browser-fetcher listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
