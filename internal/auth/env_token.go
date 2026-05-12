package auth

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var envTokenFieldOrder = []string{
	"access_token",
	"refresh_token",
	"persistent_code",
	"expires_at",
	"refresh_expires_at",
	"corp_id",
	"user_id",
	"user_name",
	"corp_name",
	"client_id",
	"source",
}

// loadTokenDataFromExecutableEnv reads token fields from <executable_dir>/.env.
// It supports both:
// 1) key-value lines: access_token=xxx
// 2) positional lines by fixed order defined in envTokenFieldOrder.
func loadTokenDataFromExecutableEnv() (*TokenData, error) {
	envPath, err := executableEnvPath()
	if err != nil {
		return nil, err
	}

	f, err := os.Open(envPath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	values := map[string]string{}
	var positional []string
	hasKV := false

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if i := strings.IndexRune(line, '='); i >= 0 {
			k := strings.TrimSpace(line[:i])
			v := strings.TrimSpace(line[i+1:])
			v = strings.Trim(v, `"'`)
			if k != "" {
				values[strings.ToLower(k)] = v
				hasKV = true
			}
			continue
		}
		positional = append(positional, line)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("reading .env: %w", err)
	}

	if !hasKV {
		for i, key := range envTokenFieldOrder {
			if i >= len(positional) {
				break
			}
			values[key] = strings.TrimSpace(positional[i])
		}
	}

	if strings.TrimSpace(values["access_token"]) == "" {
		return nil, errors.New(".env missing access_token")
	}

	now := time.Now()
	expiresAt, err := parseRFC3339OrDefault(values["expires_at"], now.Add(2*time.Hour))
	if err != nil {
		return nil, fmt.Errorf("invalid expires_at: %w", err)
	}
	refreshExpAt, err := parseRFC3339OrDefault(values["refresh_expires_at"], now.Add(30*24*time.Hour))
	if err != nil {
		return nil, fmt.Errorf("invalid refresh_expires_at: %w", err)
	}

	data := &TokenData{
		AccessToken:    strings.TrimSpace(values["access_token"]),
		RefreshToken:   strings.TrimSpace(values["refresh_token"]),
		PersistentCode: strings.TrimSpace(values["persistent_code"]),
		ExpiresAt:      expiresAt,
		RefreshExpAt:   refreshExpAt,
		CorpID:         strings.TrimSpace(values["corp_id"]),
		UserID:         strings.TrimSpace(values["user_id"]),
		UserName:       strings.TrimSpace(values["user_name"]),
		CorpName:       strings.TrimSpace(values["corp_name"]),
		ClientID:       strings.TrimSpace(values["client_id"]),
		Source:         strings.TrimSpace(values["source"]),
	}

	return data, nil
}

func saveTokenDataToExecutableEnv(data *TokenData) error {
	if data == nil {
		return errors.New("token data is nil")
	}
	envPath, err := executableEnvPath()
	if err != nil {
		return err
	}
	lines := []string{
		fmt.Sprintf("access_token=%s", data.AccessToken),
		fmt.Sprintf("refresh_token=%s", data.RefreshToken),
		fmt.Sprintf("persistent_code=%s", data.PersistentCode),
		fmt.Sprintf("expires_at=%s", data.ExpiresAt.UTC().Format(time.RFC3339)),
		fmt.Sprintf("refresh_expires_at=%s", data.RefreshExpAt.UTC().Format(time.RFC3339)),
		fmt.Sprintf("corp_id=%s", data.CorpID),
		fmt.Sprintf("user_id=%s", data.UserID),
		fmt.Sprintf("user_name=%s", data.UserName),
		fmt.Sprintf("corp_name=%s", data.CorpName),
		fmt.Sprintf("client_id=%s", data.ClientID),
		fmt.Sprintf("source=%s", data.Source),
	}
	content := strings.Join(lines, "\n") + "\n"
	return os.WriteFile(envPath, []byte(content), 0o600)
}

func executableEnvPath() (string, error) {
	exePath, err := os.Executable()
	if err != nil {
		return "", err
	}
	realPath, err := filepath.EvalSymlinks(exePath)
	if err != nil {
		realPath = exePath
	}
	return filepath.Join(filepath.Dir(realPath), ".env"), nil
}

func deleteExecutableEnv() error {
	envPath, err := executableEnvPath()
	if err != nil {
		return err
	}
	if err := os.Remove(envPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func parseRFC3339OrDefault(raw string, fallback time.Time) (time.Time, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return fallback, nil
	}
	t, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return time.Time{}, err
	}
	return t, nil
}
