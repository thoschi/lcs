package main

import (
	"bufio"
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

const version = "0.8.3"

// defaultServer kann beim Bauen mit -ldflags "-X main.defaultServer=..." gesetzt werden.
var defaultServer string

// defaultCAPEMBase64 wird vom Build-Skript mit den vertrauenswürdigen CAs gefüllt.
var defaultCAPEMBase64 string

type config struct {
	server, token, tokenFile, startConf, wrapper string
	heartbeat, poll                              time.Duration
}

type agent struct {
	config
	client                *http.Client
	deviceID, deviceToken string
	queued                map[int64]bool
	actions               chan action
	mu                    sync.Mutex
}

type operatingSystem struct {
	Position    int    `json:"position"`
	Name        string `json:"name"`
	Version     string `json:"version"`
	Description string `json:"description"`
	Image       string `json:"image"`
}

type capability struct {
	ID             string `json:"id"`
	Version        string `json:"version"`
	Title          string `json:"title"`
	Description    string `json:"description"`
	UserExecutable bool   `json:"user_executable"`
}

type action struct {
	ID           int64           `json:"id"`
	CapabilityID string          `json:"capability_id"`
	Parameters   json.RawMessage `json:"parameters"`
	RunAt        int64           `json:"run_at"`
}

var capabilities = []capability{
	{"shutdown", "1", "Herunterfahren", "Fährt den LINBO-Client herunter.", false},
	{"reboot", "1", "Neu starten", "Startet den LINBO-Client neu.", false},
	{"linbo_start_1", "3", "Starten 1", "Startet Betriebssystem 1.", false},
	{"linbo_start_2", "3", "Starten 2", "Startet Betriebssystem 2.", false},
	{"linbo_start_3", "3", "Starten 3", "Startet Betriebssystem 3.", false},
	{"linbo_sync_1", "3", "Synchronisieren 1", "Synchronisiert Betriebssystem 1.", false},
	{"linbo_sync_2", "3", "Synchronisieren 2", "Synchronisiert Betriebssystem 2.", false},
	{"linbo_sync_3", "3", "Synchronisieren 3", "Synchronisiert Betriebssystem 3.", false},
	{"linbo_format_1", "3", "Formatieren 1", "Formatiert Betriebssystem 1.", false},
	{"linbo_format_2", "3", "Formatieren 2", "Formatiert Betriebssystem 2.", false},
	{"linbo_format_3", "3", "Formatieren 3", "Formatiert Betriebssystem 3.", false},
	{"linbo_new_1", "3", "Neu 1", "Erstellt Betriebssystem 1 neu.", false},
	{"linbo_new_2", "3", "Neu 2", "Erstellt Betriebssystem 2 neu.", false},
	{"linbo_new_3", "3", "Neu 3", "Erstellt Betriebssystem 3 neu.", false},
}

func main() {
	cfg := config{}
	server := os.Getenv("LCS_SERVER")
	if server == "" {
		server = defaultServer
	}
	flag.StringVar(&cfg.server, "server", server, "URL des LCS-Servers")
	flag.StringVar(&cfg.token, "token", os.Getenv("LCS_TOKEN"), "Enrollment-Token")
	flag.StringVar(&cfg.tokenFile, "token-file", os.Getenv("LCS_TOKEN_FILE"), "Datei mit Enrollment-Token")
	flag.StringVar(&cfg.startConf, "start-conf", "/start.conf", "LINBO start.conf")
	flag.StringVar(&cfg.wrapper, "wrapper", "/usr/bin/linbo_wrapper", "LINBO-Wrapper")
	flag.DurationVar(&cfg.heartbeat, "heartbeat", 20*time.Second, "Heartbeat-Intervall")
	flag.DurationVar(&cfg.poll, "poll", 10*time.Second, "Abfrageintervall")
	flag.Parse()
	args := flag.Args()
	if cfg.server == "" {
		if len(args) > 0 {
			cfg.server = args[0]
		}
		if cfg.token == "" && len(args) > 1 {
			cfg.token = args[1]
		}
	} else if cfg.token == "" && len(args) > 0 {
		if info, err := os.Stat(args[0]); err == nil && !info.IsDir() {
			cfg.tokenFile = args[0]
		} else {
			cfg.token = args[0]
		}
	}
	if cfg.token == "" && cfg.tokenFile != "" {
		data, err := os.ReadFile(cfg.tokenFile)
		if err != nil {
			log.Fatalf("Token-Datei kann nicht gelesen werden: %v", err)
		}
		cfg.token = strings.TrimSpace(strings.SplitN(string(data), "\n", 2)[0])
	}
	if cfg.server == "" || cfg.token == "" {
		log.Fatal("-server sowie -token oder -token-file sind erforderlich")
	}
	cfg.server = strings.TrimRight(cfg.server, "/")
	client, err := httpClient()
	if err != nil {
		log.Fatalf("TLS-Vertrauensspeicher kann nicht geladen werden: %v", err)
	}
	a := &agent{
		config: cfg, client: client,
		queued: make(map[int64]bool), actions: make(chan action, 50),
	}
	go a.executeActions()
	for {
		if err := a.enroll(); err != nil {
			log.Printf("Registrierung fehlgeschlagen: %v", err)
			time.Sleep(5 * time.Second)
			continue
		}
		if err := a.run(); err != nil {
			log.Printf("Verbindung wird neu aufgebaut: %v", err)
			time.Sleep(3 * time.Second)
		}
	}
}

func httpClient() (*http.Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if defaultCAPEMBase64 != "" {
		pem, err := base64.StdEncoding.DecodeString(defaultCAPEMBase64)
		if err != nil {
			return nil, fmt.Errorf("eingebaute CA-Daten sind ungültig: %w", err)
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM(pem) {
			return nil, errors.New("eingebaute CA-Daten enthalten kein Zertifikat")
		}
		transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	}
	return &http.Client{Timeout: 30 * time.Second, Transport: transport}, nil
}

func (a *agent) request(method, path string, payload any, result any) (int, error) {
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return 0, err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequest(method, a.server+path, body)
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if a.deviceID != "" {
		req.Header.Set("X-Device-ID", a.deviceID)
		req.Header.Set("Authorization", "Bearer "+a.deviceToken)
	}
	resp, err := a.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return resp.StatusCode, fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(message)))
	}
	if result != nil {
		return resp.StatusCode, json.NewDecoder(resp.Body).Decode(result)
	}
	return resp.StatusCode, nil
}

func (a *agent) enroll() error {
	hostname, err := os.Hostname()
	if err != nil {
		return err
	}
	request := map[string]any{"enrollment_token": a.token, "hostname": hostname, "platform": "linbo", "agent_version": version}
	var response struct {
		DeviceID    string `json:"device_id"`
		DeviceToken string `json:"device_token"`
	}
	_, err = a.request(http.MethodPost, "/api/v1/enroll", request, &response)
	if err != nil {
		return err
	}
	if response.DeviceID == "" || response.DeviceToken == "" {
		return errors.New("unvollständige Registrierungsantwort")
	}
	a.deviceID, a.deviceToken = response.DeviceID, response.DeviceToken
	log.Printf("Registriert als %s", a.deviceID)
	return nil
}

func (a *agent) run() error {
	heartbeat := time.NewTicker(a.heartbeat)
	poll := time.NewTicker(a.poll)
	defer heartbeat.Stop()
	defer poll.Stop()
	if err := a.sendHeartbeat(); err != nil {
		return err
	}
	if err := a.pollActions(); err != nil {
		return err
	}
	for {
		select {
		case <-heartbeat.C:
			if err := a.sendHeartbeat(); err != nil {
				return err
			}
		case <-poll.C:
			if err := a.pollActions(); err != nil {
				return err
			}
		}
	}
}

func (a *agent) sendHeartbeat() error {
	hostname, _ := os.Hostname()
	hardware := a.inventory(hostname)
	payload := map[string]any{
		"agent_version": version, "hostname": hostname, "logged_in_users": []string{},
		"capabilities": capabilities, "hardware": hardware,
	}
	_, err := a.request(http.MethodPost, "/api/v1/heartbeat", payload, nil)
	return err
}

func (a *agent) inventory(hostname string) map[string]any {
	addresses, macs := []string{}, []string{}
	if interfaces, err := net.Interfaces(); err == nil {
		for _, iface := range interfaces {
			if iface.Flags&net.FlagLoopback != 0 {
				continue
			}
			if iface.HardwareAddr.String() != "" {
				macs = append(macs, iface.HardwareAddr.String())
			}
			if values, err := iface.Addrs(); err == nil {
				for _, value := range values {
					addresses = append(addresses, strings.Split(value.String(), "/")[0])
				}
			}
		}
	}
	serial, _ := os.ReadFile("/sys/class/dmi/id/product_serial")
	return map[string]any{
		"hostname": hostname, "operating_system": "LINBO", "os_release": "LINBO " + version,
		"architecture": runtime.GOARCH, "serial_number": strings.TrimSpace(string(serial)),
		"ip_addresses": addresses, "mac_addresses": macs,
		"linbo_operating_systems": readOperatingSystems(a.startConf), "capabilities": capabilities,
	}
}

func readOperatingSystems(path string) []operatingSystem {
	file, err := os.Open(path)
	if err != nil {
		return []operatingSystem{}
	}
	defer file.Close()
	items := []operatingSystem{}
	var current *operatingSystem
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.EqualFold(line, "[OS]") {
			items = append(items, operatingSystem{Position: len(items) + 1})
			current = &items[len(items)-1]
			continue
		}
		if current == nil {
			continue
		}
		key, value, found := strings.Cut(line, "=")
		if !found {
			continue
		}
		switch strings.ToLower(strings.TrimSpace(key)) {
		case "name":
			current.Name = strings.TrimSpace(value)
		case "version":
			current.Version = strings.TrimSpace(value)
		case "description":
			current.Description = strings.TrimSpace(value)
		case "baseimage":
			current.Image = strings.TrimSpace(value)
		}
	}
	return items
}

func (a *agent) pollActions() error {
	var response struct {
		Actions []action `json:"actions"`
	}
	_, err := a.request(http.MethodGet, "/api/v1/agent/poll", nil, &response)
	if err != nil {
		return err
	}
	for _, item := range response.Actions {
		a.mu.Lock()
		known := a.queued[item.ID]
		if !known {
			a.queued[item.ID] = true
		}
		a.mu.Unlock()
		if !known {
			a.actions <- item
		}
	}
	return nil
}

func (a *agent) executeActions() {
	for item := range a.actions {
		if delay := time.Until(time.Unix(item.RunAt, 0)); delay > 0 {
			time.Sleep(delay)
		}
		accepted := false
		for {
			var err error
			accepted, err = a.acknowledge(item.ID)
			if err == nil {
				break
			}
			log.Printf("Aktion %d konnte nicht bestätigt werden: %v", item.ID, err)
			time.Sleep(3 * time.Second)
		}
		if accepted {
			message, err := a.execute(item, func() {
				for a.report(item.ID, item.CapabilityID+" lokal an "+a.wrapper+" übergeben", nil) != nil {
					time.Sleep(3 * time.Second)
				}
			})
			for err != nil || strings.TrimSpace(message) != "" {
				if a.report(item.ID, message, err) == nil {
					break
				}
				time.Sleep(3 * time.Second)
			}
		}
		a.mu.Lock()
		delete(a.queued, item.ID)
		a.mu.Unlock()
	}
}

func (a *agent) acknowledge(id int64) (bool, error) {
	var response struct {
		ActionIDs []int64 `json:"action_ids"`
	}
	_, err := a.request(http.MethodPost, "/api/v1/action/ack", map[string]any{"action_ids": []int64{id}}, &response)
	if err != nil {
		return false, err
	}
	for _, accepted := range response.ActionIDs {
		if accepted == id {
			return true, nil
		}
	}
	return false, nil
}

func (a *agent) execute(item action, started func()) (string, error) {
	commands := []string{}
	switch item.CapabilityID {
	case "shutdown":
		commands = []string{"halt"}
	case "reboot":
		commands = []string{"reboot"}
	default:
		parts := strings.Split(item.CapabilityID, "_")
		if len(parts) != 3 || parts[0] != "linbo" {
			return "", fmt.Errorf("Fähigkeit ist lokal nicht vorhanden: %s", item.CapabilityID)
		}
		position, err := strconv.Atoi(parts[2])
		if err != nil || position < 1 || position > 3 || position > len(readOperatingSystems(a.startConf)) {
			return "", errors.New("ungültige Betriebssystemposition")
		}
		if parts[1] != "start" && parts[1] != "sync" && parts[1] != "format" && parts[1] != "new" {
			return "", fmt.Errorf("Fähigkeit ist lokal nicht vorhanden: %s", item.CapabilityID)
		}
		commands = []string{parts[1] + ":" + strconv.Itoa(position)}
	}
	var output bytes.Buffer
	command := exec.Command(a.wrapper, commands...)
	command.Stdout, command.Stderr = &output, &output
	if err := command.Start(); err != nil {
		return err.Error(), err
	}
	started()
	err := command.Wait()
	message := output.String()
	if len(message) > 4096 {
		message = message[len(message)-4096:]
	}
	if err != nil && strings.TrimSpace(message) == "" {
		message = err.Error()
	}
	return message, err
}

func (a *agent) report(id int64, message string, actionErr error) error {
	result := map[string]any{"message": message}
	if actionErr != nil {
		result["error"] = actionErr.Error()
	}
	payload := map[string]any{"action_id": id, "ok": actionErr == nil, "result": result}
	if _, err := a.request(http.MethodPost, "/api/v1/action/result", payload, nil); err != nil {
		log.Printf("Ergebnis für Aktion %d konnte nicht gemeldet werden: %v", id, err)
		return err
	}
	return nil
}
