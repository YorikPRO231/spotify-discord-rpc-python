import shutil

import requests
import json
import time
import os
import webbrowser
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pypresence import Presence, ActivityType
from pathlib import Path
import base64
import sys
import hashlib

logging.basicConfig (
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler ('spotify_discord_status.log'),
        logging.StreamHandler ()
    ]
)
logger = logging.getLogger (__name__)


class SettingsManager:
    def __init__(self):
        self.settings_file = Path ('settings.json')
        self.default_settings = {
            "spotify": {
                "client_id": "your_client_id",
                "client_secret": "your_client_secret"
            },
            "discord": {
                "client_id": "1359268232959365301"
            },
            "server": {
                "redirect_uri": "http://127.0.0.1:8888/callback",
                "redirect_port": 8888
            }
        }

    def create_default_settings(self):
        if not self.settings_file.exists ():
            with open (self.settings_file, 'w', encoding='utf-8') as f:
                json.dump (self.default_settings, f, indent=4)
            logger.info ("Created default settings.json")
            return False
        return True

    def load_settings(self):
        try:
            if not self.settings_file.exists ():
                self.create_default_settings ()

            with open (self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load (f)

            required = [
                "spotify.client_id",
                "spotify.client_secret",
                "discord.client_id",
                "server.redirect_uri",
                "server.redirect_port"
            ]

            for param in required:
                keys = param.split ('.')
                current = settings
                for key in keys:
                    if key not in current:
                        raise ValueError (f"Missing required parameter: {param}")
                    current = current[key]

            return settings

        except Exception as e:
            logger.error (f"Error loading settings: {e}")
            return None


class AutoUpdater:
    def __init__(self, repo_url):
        self.repo_url = repo_url
        self.is_exe = getattr (sys, 'frozen', False)
        self.current_version = self.get_current_version ()
        self.update_in_progress = False
        self.last_check_file = Path (sys.executable).parent / 'last_update_check' if self.is_exe else None
        self.max_retries = 3
        self.retry_delay = 5
        self.update_check_interval = 86400  # 24 часа в секундах
        self.user_agent = "SpotifyDiscordRPC/1.0"

    def get_current_version(self):
        """Получает текущую версию приложения"""
        try:
            if self.is_exe:
                version_file = Path (sys.executable).parent / 'version.txt'
                if version_file.exists ():
                    with open (version_file, 'r') as f:
                        version = f.read ().strip ()
                        if version:  # Проверяем, что файл не пустой
                            return version
                # Возвращаем версию по умолчанию, если файл не существует или пуст
                return "1.1.0"
            else:
                # Для исходного кода используем хеш файла
                with open (__file__, 'rb') as f:
                    return hashlib.md5 (f.read ()).hexdigest ()
        except Exception as e:
            logger.error (f"Version detection error: {e}")
            return "unknown"

    def should_check_for_updates(self):
        """Определяет, нужно ли проверять обновления"""
        if not self.is_exe:
            return True  # Для исходного кода проверяем всегда

        if not self.last_check_file.exists ():
            return True

        try:
            last_check = float (self.last_check_file.read_text ())
            return (time.time () - last_check) > self.update_check_interval
        except Exception as e:
            logger.error (f"Error reading last check time: {e}")
            return True

    def check_for_updates(self, force=False):
        """Основной метод проверки обновлений"""
        if not self.repo_url or self.update_in_progress:
            return False

        try:
            if not force and not self.should_check_for_updates ():
                return False

            logger.info ("Checking for updates...")

            if self.is_exe:
                return self.check_exe_updates ()
            else:
                return self.check_source_updates ()

        except Exception as e:
            logger.error (f"Update check failed: {e}")
            return False
        finally:
            if self.is_exe and self.last_check_file:
                self.update_last_check_time ()

    def check_exe_updates(self):
        """Проверка обновлений для исполняемого файла"""
        try:
            repo_path = self.repo_url.split ('github.com/')[1]
            releases_api = f"https://api.github.com/repos/{repo_path}/releases/latest"

            response = self.make_http_request (releases_api)
            if not response:
                return False

            release_data = response.json ()
            latest_version = release_data.get ('tag_name')

            if not latest_version:
                logger.error ("No version tag found in release data")
                return False

            if latest_version != self.current_version:
                logger.info (f"New version available: {latest_version} (current: {self.current_version})")
                return self.download_exe_update (release_data)

            logger.info ("No updates available")
            return False

        except Exception as e:
            logger.error (f"EXE update check failed: {e}")
            return False

    def check_source_updates(self):
        """Проверка обновлений для исходного кода"""
        try:
            repo_path = self.repo_url.split ('github.com/')[1]
            raw_url = f"https://raw.githubusercontent.com/{repo_path}/main/main.py"

            response = self.make_http_request (raw_url)
            if not response:
                return False

            remote_hash = hashlib.md5 (response.content).hexdigest ()
            if remote_hash != self.current_version:
                logger.info ("Source code update available")
                return self.update_source (response.content)

            logger.info ("Source code is up to date")
            return False

        except Exception as e:
            logger.error (f"Source update check failed: {e}")
            return False

    def make_http_request(self, url):
        """Выполняет HTTP-запрос с повторными попытками"""
        for attempt in range (self.max_retries):
            try:
                headers = {'User-Agent': self.user_agent}
                response = requests.get (url, headers=headers, timeout=10)
                response.raise_for_status ()
                return response
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    logger.warning (f"Request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                    time.sleep (self.retry_delay)
                else:
                    raise
        return None

    def download_exe_update(self, release_data):
        """Загружает и устанавливает обновление для исполняемого файла"""
        self.update_in_progress = True
        try:
            exe_asset = next (
                (asset for asset in release_data['assets']
                 if asset['name'].lower ().endswith ('.exe') and 'spotifydiscordrpc' in asset['name'].lower ()),
                None
            )

            if not exe_asset:
                logger.error ("Correct EXE asset not found in release")
                return False

            download_url = exe_asset['browser_download_url']
            temp_exe = Path (sys.executable).parent / 'SpotifyDiscordRPC_new.exe'

            logger.info (f"Downloading update from {download_url}")
            response = self.make_http_request (download_url)
            if not response:
                return False

            temp_download = temp_exe.with_name (f"{temp_exe.stem}_{int (time.time ())}{temp_exe.suffix}")

            try:
                with open (temp_download, 'wb') as f:
                    for chunk in response.iter_content (chunk_size=8192):
                        f.write (chunk)

                if not temp_download.exists ():
                    logger.error ("Downloaded file not found")
                    return False

                # Проверяем, что файл не пустой
                if temp_download.stat ().st_size == 0:
                    logger.error ("Downloaded file is empty")
                    temp_download.unlink ()
                    return False

                # Переименовываем во временный файл
                if temp_exe.exists ():
                    temp_exe.unlink ()
                temp_download.rename (temp_exe)

                return self.create_updater_script (temp_exe)

            except Exception as e:
                if temp_download.exists ():
                    temp_download.unlink ()
                raise

        except Exception as e:
            logger.error (f"Update download failed: {e}")
            return False
        finally:
            sys.exit (0)

    def create_updater_script(self, temp_exe):
        """Создает скрипт для обновления"""
        try:
            bat_content = f"""
            @echo off
            chcp 65001 >nul
            echo Updating Spotify Discord RPC...
            timeout /t 3 /nobreak >nul

            :kill_process
            taskkill /F /IM "{Path (sys.executable).name}" >nul 2>&1
            if %errorlevel% neq 0 goto check_process

            :check_process
            tasklist | find "{Path (sys.executable).stem}" >nul
            if %errorlevel% eq 0 (
                timeout /t 1 /nobreak >nul
                goto kill_process
            )

            :replace_file
            del "{sys.executable}" >nul 2>&1
            if exist "{sys.executable}" (
                timeout /t 1 /nobreak >nul
                goto replace_file
            )

            rename "{temp_exe}" "{Path (sys.executable).name}"

            :start_app
            start "" "{Path (sys.executable).name}"

            :cleanup
            del "%~f0"
            exit
            """

            bat_path = Path (sys.executable).parent / 'update.bat'
            with open (bat_path, 'w', encoding='utf-8') as f:
                f.write (bat_content)

            logger.info ("Launching updater script")
            os.startfile (bat_path)
            return True

        except Exception as e:
            logger.error (f"Failed to create updater script: {e}")
            return False

    def update_source(self, new_content):
        """Обновляет исходный код"""
        try:
            backup_file = f"{__file__}.bak"
            if os.path.exists (backup_file):
                os.remove (backup_file)

            shutil.copy2 (__file__, backup_file)

            # Записываем новый контент
            with open (__file__, 'wb') as f:
                f.write (new_content)

            logger.info ("Source updated successfully. Please restart the application.")
            return True

        except Exception as e:
            logger.error (f"Source update failed: {e}")
            # Пытаемся восстановить из резервной копии
            if os.path.exists (backup_file):
                try:
                    shutil.copy2 (backup_file, __file__)
                    logger.info ("Restored from backup after failed update")
                except Exception as restore_error:
                    logger.error (f"Failed to restore from backup: {restore_error}")
            return False

    def update_last_check_time(self):
        """Обновляет время последней проверки обновлений"""
        try:
            with open (self.last_check_file, 'w') as f:
                f.write (str (time.time ()))
        except Exception as e:
            logger.error (f"Failed to update last check time: {e}")


class CallbackHandler (BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse (self.path)
        if parsed_path.path == '/callback':
            query = parse_qs (parsed_path.query)
            code = query.get ('code', [None])[0]

            if code:
                self.send_response (200)
                self.send_header ('Content-type', 'text/html; charset=utf-8')
                self.end_headers ()
                response = """
                <html><body>
                <h1>Success!</h1>
                <p>Close this window and return to the app.</p>
                </body></html>
                """
                self.wfile.write (response.encode ('utf-8'))

                with open ('callback_code.txt', 'w') as f:
                    f.write (code)
            else:
                self.send_response (400)
                self.send_header ('Content-type', 'text/html; charset=utf-8')
                self.end_headers ()
                response = """
                <html><body>
                <h1>Error</h1>
                <p>Authorization failed.</p>
                </body></html>
                """
                self.wfile.write (response.encode ('utf-8'))

            threading.Thread (target=self.server.shutdown, daemon=True).start ()
        else:
            self.send_response (404)
            self.end_headers ()


class SpotifyNowPlaying:
    def __init__(self, settings):
        self.settings = settings
        self.access_token = None
        self.refresh_token = None
        self.token_expires = 0
        self.discord_rpc = None
        self.data_file = self.get_appdata_path () / 'spotify_discord_status.json'
        self.ensure_data_file ()
        self.current_track_id = None
        self.track_start_time = None
        self.running = False

        self.SPOTIFY_CLIENT_ID = self.settings['spotify']['client_id']
        self.SPOTIFY_CLIENT_SECRET = self.settings['spotify']['client_secret']
        self.DISCORD_CLIENT_ID = self.settings['discord']['client_id']
        self.REDIRECT_URI = self.settings['server']['redirect_uri']
        self.REDIRECT_PORT = int (self.settings['server']['redirect_port'])
        self.SCOPE = 'user-read-currently-playing user-read-playback-state'
        self.AUTH_URL = 'https://accounts.spotify.com/authorize'
        self.TOKEN_URL = 'https://accounts.spotify.com/api/token'

        self.connect_discord ()

    def connect_discord(self):
        try:
            self.discord_rpc = Presence (self.DISCORD_CLIENT_ID)
            self.discord_rpc.connect ()
            logger.info ("Connected to Discord RPC")
        except Exception as e:
            logger.error (f"Discord connection error: {e}")
            self.discord_rpc = None

    def get_appdata_path(self):
        appdata_path = Path (os.getenv ('LOCALAPPDATA')) / 'SpotifyDiscordStatus'
        appdata_path.mkdir (exist_ok=True)
        return appdata_path

    def ensure_data_file(self):
        if not self.data_file.exists ():
            with open (self.data_file, 'w', encoding='utf-8') as f:
                json.dump ({"refresh_token": None}, f, ensure_ascii=False, indent=2)
        else:
            with open (self.data_file, 'r', encoding='utf-8') as f:
                data = json.load (f)
                self.refresh_token = data.get ('refresh_token')

    def save_tokens_to_file(self):
        if not self.refresh_token:
            return

        try:
            with open (self.data_file, 'w', encoding='utf-8') as f:
                json.dump ({"refresh_token": self.refresh_token}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error (f"Error saving tokens: {e}")

    def get_auth_code(self):
        auth_params = {
            'client_id': self.SPOTIFY_CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': self.REDIRECT_URI,
            'scope': self.SCOPE,
            'show_dialog': 'true'
        }

        auth_url = f"{self.AUTH_URL}?{requests.compat.urlencode (auth_params)}"
        logger.info (f"Opening browser for authorization: {auth_url}")
        webbrowser.open (auth_url)

        server = HTTPServer (('127.0.0.1', self.REDIRECT_PORT), CallbackHandler)
        logger.info (f"Server started on {self.REDIRECT_URI}, waiting for code...")

        server.timeout = 30
        server.handle_request ()

        if os.path.exists ('callback_code.txt'):
            with open ('callback_code.txt', 'r') as f:
                code = f.read ()
            os.remove ('callback_code.txt')
            return code
        return None

    def get_initial_tokens(self, auth_code):
        if not auth_code:
            return False

        auth_header = base64.b64encode (f"{self.SPOTIFY_CLIENT_ID}:{self.SPOTIFY_CLIENT_SECRET}".encode ()).decode ()

        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.REDIRECT_URI
        }

        try:
            response = requests.post (self.TOKEN_URL, headers=headers, data=data, timeout=10)
            if response.status_code == 200:
                token_data = response.json ()
                self.access_token = token_data['access_token']
                self.refresh_token = token_data.get ('refresh_token', self.refresh_token)
                self.token_expires = time.time () + token_data['expires_in']
                self.save_tokens_to_file ()
                return True
            logger.error (f"Token request failed: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error (f"Token request error: {e}")
        return False

    def get_spotify_access_token(self):
        if not self.refresh_token:
            auth_code = self.get_auth_code ()
            return self.get_initial_tokens (auth_code)

        auth_header = base64.b64encode (f"{self.SPOTIFY_CLIENT_ID}:{self.SPOTIFY_CLIENT_SECRET}".encode ()).decode ()

        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token
        }

        try:
            response = requests.post (self.TOKEN_URL, headers=headers, data=data, timeout=10)
            if response.status_code == 200:
                token_data = response.json ()
                self.access_token = token_data['access_token']
                self.token_expires = time.time () + token_data['expires_in']
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data['refresh_token']
                    self.save_tokens_to_file ()
                return True
            logger.error (f"Token refresh failed: {response.status_code} - {response.text}")
            self.refresh_token = None
        except Exception as e:
            logger.error (f"Token refresh error: {e}")
        return False

    def check_token(self):
        if not self.access_token or time.time () >= self.token_expires:
            return self.get_spotify_access_token ()
        return True

    def get_current_track(self):
        if not self.check_token ():
            return None

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.get (
                'https://api.spotify.com/v1/me/player/currently-playing',
                headers=headers,
                timeout=5
            )
            if response.status_code == 200:
                return response.json ()
            elif response.status_code == 204:
                logger.info ("No track currently playing")
            else:
                logger.error (f"Track request failed: {response.status_code}")
        except Exception as e:
            logger.error (f"Track request error: {e}")
        return None

    def update_discord_status(self, track_info):
        if not self.discord_rpc:
            return

        if not track_info:
            try:
                self.discord_rpc.clear ()
                self.current_track_id = None
            except Exception as e:
                logger.error (f"Discord clear error: {e}")
            return

        current_track_id = track_info['item']['id']

        if current_track_id != self.current_track_id:
            self.current_track_id = current_track_id
            progress_ms = track_info.get ('progress_ms', 0)
            self.track_start_time = int (time.time () - (progress_ms / 1000))

        try:
            self.discord_rpc.update (
                activity_type=ActivityType.LISTENING,
                details=track_info['item']['name'][:128],
                state=', '.join ([a['name'] for a in track_info['item']['artists']])[:128],
                large_image=track_info['item']['album']['images'][0]['url'] if track_info['item']['album'][
                    'images'] else None,
                large_text=track_info['item']['album']['name'][:128],
                small_image='spotify',
                small_text='Listening on Spotify',
                start=self.track_start_time,
                end=self.track_start_time + (track_info['item']['duration_ms'] // 1000),
                buttons=[{
                    'label': 'Listen on Spotify',
                    'url': track_info['item']['external_urls']['spotify']
                }]
            )
        except Exception as e:
            logger.error (f"Discord update error: {e}")

    def cleanup(self):
        if self.discord_rpc:
            try:
                self.discord_rpc.clear ()
                self.discord_rpc.close ()
            except Exception as e:
                logger.error (f"RPC close error: {e}")
        if os.path.exists ('callback_code.txt'):
            try:
                os.remove ('callback_code.txt')
            except:
                pass

    def run(self):
        logger.info ("Spotify Discord Status Updater started...")
        logger.info ("Press Ctrl+C to exit")
        self.running = True

        try:
            if not self.refresh_token:
                logger.info ("Spotify authorization required...")
                auth_code = self.get_auth_code ()
                if not self.get_initial_tokens (auth_code):
                    logger.error ("Failed to get tokens. Check settings.")
                    return

            while self.running:
                try:
                    track_info = self.get_current_track ()
                    self.update_discord_status (track_info)

                    for _ in range (10):
                        if not self.running:
                            break
                        time.sleep (1)

                except KeyboardInterrupt:
                    self.running = False
                except Exception as e:
                    logger.error (f"Main loop error: {str (e)}")
                    time.sleep (10)

        finally:
            self.cleanup ()
            logger.info ("Application stopped")


def main():
    settings_manager = SettingsManager ()
    settings = settings_manager.load_settings ()
    if not settings:
        sys.exit (1)

    repo_url = "https://github.com/YorikPRO231/spotify-discord-rpc-python"
    updater = AutoUpdater (repo_url)

    if updater.check_for_updates ():
        sys.exit (0)

    spotify_status = SpotifyNowPlaying (settings)
    try:
        spotify_status.run ()
    except KeyboardInterrupt:
        spotify_status.cleanup ()
        logger.info ("\nStopped by user request")
    except Exception as e:
        spotify_status.cleanup ()
        logger.error (f"Critical error: {e}")
        sys.exit (1)


if __name__ == "__main__":
    main ()
