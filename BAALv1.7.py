import base64
import csv
import os
import random
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

import cfbd
import certifi
import folium
import requests
from bs4 import BeautifulSoup
from cfbd.rest import ApiException
from cfbd.exceptions import UnauthorizedException
from googlesearch import search
from PIL import Image
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


BASE_DIR = Path(__file__).resolve().parent
STADIUMS_CSV = BASE_DIR / "stadiums-geocoded.csv"
LOGOS_CSV = BASE_DIR / "logos" / "logos.csv"
DEFAULT_LOCATION = (34.0522, -118.2437)
DEFAULT_PRIMARY_COLOR = "#ffffff"
DEFAULT_SECONDARY_COLOR = "#000000"
HEX_DIGITS = set("0123456789abcdefABCDEF")
NON_PLAYER_IMAGE_TERMS = ("logo", "icon", "sprite", "avatar", "placeholder", "loading")
STAT_CATEGORIES = (
    "rushing",
    "passing",
    "defensive",
    "receiving",
    "special teams",
    "blocking",
    "offensive",
    "defense",
)


def normalized(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def css_hex_color(value: Optional[str], fallback: str) -> str:
    color = (value or "").strip()
    if not color:
        return fallback

    if not color.startswith("#"):
        color = f"#{color}"

    hex_value = color[1:]
    if len(hex_value) in (3, 6) and all(char in HEX_DIGITS for char in hex_value):
        return color
    return fallback


def first_value(data: dict, *keys: str, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def object_to_dict(value) -> dict:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {}


def user_facing_error(error: Exception) -> str:
    if isinstance(error, UnauthorizedException):
        return "CFBD rejected the API key. Set CFBD_API_KEY to a valid CollegeFootballData API key."
    return str(error)


@dataclass(frozen=True)
class StadiumInfo:
    team: str
    stadium: str
    conference: str
    capacity: str
    built: str
    expanded: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class LogoInfo:
    school: str
    mascot: str
    abbreviation: str
    conference: str
    division: str
    logo_url: str


@dataclass(frozen=True)
class TeamProfile:
    stadium: StadiumInfo
    logo: Optional[LogoInfo]


@dataclass(frozen=True)
class PlayerGameStat:
    player: str
    stat_type: str
    stat: str
    week: int
    year: int


class CsvTeamRepository:
    """Reads local team assets without tying CSV parsing to the UI."""

    def __init__(self, stadiums_path: Path, logos_path: Path):
        self.stadiums_path = stadiums_path
        self.logos_path = logos_path
        self._stadiums = self._load_stadiums()
        self._logos = self._load_logos()

    def find_team_profile(self, team_name: str) -> Optional[TeamProfile]:
        stadium = self.find_stadium(team_name)
        if stadium is None:
            return None
        return TeamProfile(stadium=stadium, logo=self.find_logo(team_name))

    def find_stadium(self, team_name: str) -> Optional[StadiumInfo]:
        return self._stadiums.get(normalized(team_name))

    def find_logo(self, team_name: str) -> Optional[LogoInfo]:
        return self._logos.get(normalized(team_name))

    def _load_stadiums(self) -> dict[str, StadiumInfo]:
        stadiums = {}
        with self.stadiums_path.open(newline="", encoding="utf-8") as csv_file:
            for row in csv.DictReader(csv_file):
                try:
                    info = StadiumInfo(
                        team=row.get("team", ""),
                        stadium=row.get("stadium", ""),
                        conference=row.get("conference", ""),
                        capacity=row.get("capacity", ""),
                        built=row.get("built", ""),
                        expanded=row.get("expanded", ""),
                        latitude=float(row.get("latitude") or 0),
                        longitude=float(row.get("longitude") or 0),
                    )
                except ValueError:
                    continue
                stadiums[normalized(info.team)] = info
        return stadiums

    def _load_logos(self) -> dict[str, LogoInfo]:
        logos = {}
        with self.logos_path.open(newline="", encoding="utf-8") as csv_file:
            for row in csv.DictReader(csv_file):
                info = LogoInfo(
                    school=row.get("school", ""),
                    mascot=row.get("mascot", ""),
                    abbreviation=row.get("abbreviation", ""),
                    conference=row.get("conference", ""),
                    division=row.get("division", ""),
                    logo_url=row.get("logo", ""),
                )
                for alias in self._logo_aliases(row):
                    logos[normalized(alias)] = info
        return logos

    @staticmethod
    def _logo_aliases(row: dict) -> Iterable[str]:
        fields = ("school", "abbreviation", "alt_name1", "alt_name2", "alt_name3")
        return (row.get(field, "") for field in fields if row.get(field, "").strip())


class CollegeFootballClient:
    """Small wrapper around cfbd so API details do not leak into widgets."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("CFBD_API_KEY")
        configuration = cfbd.Configuration()
        if self.api_key:
            configuration.access_token = self.api_key
            configuration.api_key["Authorization"] = self.api_key
            configuration.api_key_prefix["Authorization"] = "Bearer"
        configuration.ssl_ca_cert = certifi.where()
        self.players_api = cfbd.PlayersApi(cfbd.ApiClient(configuration))
        self.stats_api = cfbd.StatsApi(cfbd.ApiClient(configuration))

    def search_players(self, search_term: str) -> list[dict]:
        self._require_api_key()
        response = self.players_api.search_players(search_term=search_term)
        if isinstance(response, list):
            return [object_to_dict(player) for player in response if object_to_dict(player)]

        player = object_to_dict(response)
        return [player] if player else []

    def player_week_stats(
        self,
        year: int,
        team: str,
        week: int,
        category: str,
        player_name: str,
    ) -> list[PlayerGameStat]:
        self._require_api_key()
        stats = self.stats_api.get_player_season_stats(
            year,
            team=team,
            start_week=week,
            end_week=week,
            category=category,
        )
        return [
            PlayerGameStat(
                player=stat.player,
                stat_type=stat.stat_type,
                stat=str(stat.stat),
                week=week,
                year=year,
            )
            for stat in stats
            if normalized(getattr(stat, "player", "")) == normalized(player_name)
            and hasattr(stat, "stat_type")
            and hasattr(stat, "stat")
        ]

    def _require_api_key(self):
        if not self.api_key:
            raise RuntimeError("Set CFBD_API_KEY before using CollegeFootballData API features.")


class ImageService:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0 Safari/537.36"
                )
            }
        )

    def first_valid_player_image_url(self, search_term: str) -> Optional[str]:
        if not search_term:
            return None

        query = f"{search_term} action photo on field -nfl -draft -combine -headshot -portrait"
        for url in self._candidate_image_urls(query):
            if self.is_valid_image_url(url):
                return url
        return None

    def _candidate_image_urls(self, query: str) -> Iterable[str]:
        for page_url in self._search_urls(query):
            if self.is_valid_image_url(page_url):
                yield page_url

            for image_url in self._extract_page_images(page_url):
                yield image_url

    def _search_urls(self, query: str) -> Iterable[str]:
        urls = []
        try:
            urls = list(search(query, num=15, stop=15, pause=1))
        except TypeError:
            urls = list(search(query, num_results=15))
        except Exception:
            urls = []

        if urls:
            return urls
        return self._duckduckgo_search_urls(query)

    def _duckduckgo_search_urls(self, query: str) -> list[str]:
        search_url = f"https://html.duckduckgo.com/html/?{urlencode({'q': query})}"
        try:
            response = self.session.get(search_url, timeout=8)
            response.raise_for_status()
        except requests.RequestException:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        urls = []
        for link in soup.select("a.result__a"):
            href = link.get("href")
            if not href:
                continue
            urls.append(self._clean_duckduckgo_url(href))
        return urls

    @staticmethod
    def _clean_duckduckgo_url(url: str) -> str:
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.query:
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return target
        return url

    def _extract_page_images(self, page_url: str) -> Iterable[str]:
        try:
            response = self.session.get(page_url, timeout=8)
            response.raise_for_status()
        except requests.RequestException:
            return []

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        selectors = (
            ("meta", {"property": "og:image"}, "content"),
            ("meta", {"name": "twitter:image"}, "content"),
            ("meta", {"property": "twitter:image"}, "content"),
            ("img", {}, "src"),
        )

        image_urls = []
        for tag_name, attrs, value_attr in selectors:
            for tag in soup.find_all(tag_name, attrs=attrs):
                value = tag.get(value_attr)
                if value:
                    image_urls.append(urljoin(page_url, value))
        return image_urls

    def is_valid_image_url(self, url: str) -> bool:
        if any(term in url.casefold() for term in NON_PLAYER_IMAGE_TERMS):
            return False

        try:
            response = self.session.get(url, timeout=8)
            response.raise_for_status()
        except requests.RequestException:
            return False

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/") or "svg" in content_type:
            return False

        pixmap = QPixmap()
        if not pixmap.loadFromData(response.content):
            return False
        return pixmap.width() >= 300 and pixmap.height() >= 200

    def pixmap_from_url(self, url: str) -> Optional[QPixmap]:
        try:
            response = self.session.get(url, timeout=8)
            response.raise_for_status()
        except requests.RequestException:
            return None

        pixmap = QPixmap()
        pixmap.loadFromData(response.content)
        return pixmap

    def logo_as_base64_png(self, url: str, max_width: int = 100) -> Optional[str]:
        if not url:
            return None

        try:
            response = self.session.get(url, timeout=8)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
        except (requests.RequestException, OSError):
            return None

        aspect_ratio = image.width / image.height
        new_width = min(image.width, max_width)
        new_height = max(1, int(new_width / aspect_ratio))
        resized_image = image.resize((new_width, new_height))

        buffer = BytesIO()
        resized_image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


class MapRenderer:
    def __init__(self, image_service: ImageService):
        self.image_service = image_service

    def default_map(self) -> folium.Map:
        map_view = folium.Map(location=DEFAULT_LOCATION, zoom_start=12)
        folium.CircleMarker(
            DEFAULT_LOCATION,
            radius=10,
            color="red",
            fill=True,
            fill_color="red",
            popup="Los Angeles",
        ).add_to(map_view)
        return map_view

    def team_map(self, profile: TeamProfile) -> folium.Map:
        stadium = profile.stadium
        location = (stadium.latitude, stadium.longitude)
        map_view = folium.Map(location=location, zoom_start=16)
        folium.Marker(location=location, popup=self._popup(profile), tooltip=stadium.team).add_to(map_view)
        return map_view

    def _popup(self, profile: TeamProfile) -> folium.Popup:
        stadium = profile.stadium
        logo = profile.logo
        popup_lines = []

        if logo:
            logo_image = self.image_service.logo_as_base64_png(logo.logo_url)
            if logo_image:
                popup_lines.append(f'<img src="data:image/png;base64,{logo_image}" alt="{stadium.team} logo">')
            popup_lines.extend(
                [
                    f"Team Abbreviation: {logo.abbreviation}",
                    f"Team Conference: {logo.conference or stadium.conference}",
                    f"Team Division: {logo.division}",
                ]
            )

        popup_lines.extend(
            [
                f"Stadium Name: {stadium.stadium}",
                f"Capacity: {stadium.capacity}",
                f"Year Built: {stadium.built}",
                f"Last Expanded: {stadium.expanded}",
            ]
        )
        return folium.Popup("<br>".join(popup_lines), show=True)


class PlayerSearchApp(QWidget):
    def __init__(
        self,
        football_client: Optional[CollegeFootballClient] = None,
        team_repository: Optional[CsvTeamRepository] = None,
        image_service: Optional[ImageService] = None,
    ):
        super().__init__()
        self.football_client = football_client or CollegeFootballClient()
        self.team_repository = team_repository or CsvTeamRepository(STADIUMS_CSV, LOGOS_CSV)
        self.image_service = image_service or ImageService()
        self.map_renderer = MapRenderer(self.image_service)

        self.players_list: list[dict] = []
        self.current_player: Optional[dict] = None
        self.current_map = self.map_renderer.default_map()
        self.search_term = ""
        self.year = 2023
        self.stat_category = "passing"

        self.init_ui()
        self.apply_colors(DEFAULT_PRIMARY_COLOR, DEFAULT_SECONDARY_COLOR)
        self.display_map()

    def init_ui(self):
        self.setWindowTitle("BAAL Player Search")
        self.setGeometry(100, 100, 1200, 900)

        self.label = QLabel("Enter Player Name:", self)
        self.text_edit = QTextEdit(self)
        self.text_edit.setFixedHeight(34)

        self.search_button = QPushButton("Search", self)
        self.search_button.clicked.connect(self.search_player)

        self.players_combobox = QComboBox(self)
        self.players_combobox.currentIndexChanged.connect(self.display_selected_player_info)

        self.years_combobox = QComboBox(self)
        self.years_combobox.addItem("Select Year")
        for year in range(2026, 1899, -1):
            self.years_combobox.addItem(str(year))
        self.years_combobox.setCurrentText(str(self.year))
        self.years_combobox.currentIndexChanged.connect(self.year_select)

        self.stat_combobox = QComboBox(self)
        self.stat_combobox.addItem("Stat Type")
        self.stat_combobox.addItems(STAT_CATEGORIES)
        self.stat_combobox.setCurrentText(self.stat_category)
        self.stat_combobox.currentIndexChanged.connect(self.stat_select)

        self.team_button = QPushButton("Get Team", self)
        self.team_button.clicked.connect(self.show_current_team)

        self.update_button = QPushButton("Update Map", self)
        self.update_button.clicked.connect(self.update_current_team_map)

        self.random_game_button = QPushButton("Random Game", self)
        self.random_game_button.clicked.connect(self.random_game_update)

        self.clear_button = QPushButton("Clear", self)
        self.clear_button.clicked.connect(self.clear)

        self.player_table = QTableWidget(self)
        self.player_table.setColumnCount(2)
        self.player_table.setHorizontalHeaderLabels(["Attribute", "Value"])

        self.game_details_table = QTableWidget(self)
        self.game_details_table.setColumnCount(5)
        self.game_details_table.setHorizontalHeaderLabels(["Name", "Stat Type", "Stat", "Week", "Year"])

        self.team_logo_label = QLabel(self)
        self.team_logo_label.setObjectName("teamLogo")
        self.team_logo_label.setFixedSize(96, 96)
        self.team_logo_label.setAlignment(Qt.AlignCenter)

        self.image_label = QLabel(self)
        self.image_label.setObjectName("playerImage")
        self.image_label.setFixedSize(180, 180)
        self.image_label.setAlignment(Qt.AlignCenter)

        self.player_name_label = QLabel("Search for a player", self)
        self.player_name_label.setObjectName("playerName")
        self.player_name_label.setAlignment(Qt.AlignCenter)

        self.team_name_label = QLabel("", self)
        self.team_name_label.setObjectName("teamName")
        self.team_name_label.setAlignment(Qt.AlignCenter)

        image_layout = QHBoxLayout()
        image_layout.addWidget(self.team_logo_label)
        image_layout.addWidget(self.image_label)

        controls_layout = QVBoxLayout()
        controls_layout.addWidget(self.label)
        controls_layout.addWidget(self.text_edit)
        controls_layout.addWidget(self.search_button)
        controls_layout.addWidget(self.players_combobox)
        controls_layout.addWidget(self.player_name_label)
        controls_layout.addWidget(self.team_name_label)
        controls_layout.addLayout(image_layout)
        controls_layout.addWidget(self.player_table)
        controls_layout.addWidget(self.years_combobox)
        controls_layout.addWidget(self.stat_combobox)
        controls_layout.addWidget(self.random_game_button)
        controls_layout.addWidget(self.team_button)
        controls_layout.addWidget(self.clear_button)

        map_layout = QVBoxLayout()
        map_layout.addWidget(self.update_button)
        self.browser = QWebEngineView()
        map_layout.addWidget(self.browser)
        map_layout.addWidget(self.game_details_table)

        main_layout = QHBoxLayout(self)
        main_layout.addLayout(controls_layout, 1)
        main_layout.addLayout(map_layout, 2)
        self.setLayout(main_layout)

    def search_player(self):
        self.search_term = self.text_edit.toPlainText().strip()
        if not self.search_term:
            self.set_message("Enter a player name before searching.")
            return

        try:
            self.players_list = self.football_client.search_players(self.search_term)
        except Exception as error:
            self.set_message(f"Search failed: {user_facing_error(error)}")
            return

        self.populate_player_choices()
        if not self.players_list:
            self.set_message("No players found.")
            return

        self.select_player(self.players_list[0])

    def populate_player_choices(self):
        self.players_combobox.blockSignals(True)
        self.players_combobox.clear()
        self.players_combobox.addItem("Select Player", None)

        for player in self.players_list:
            label = " | ".join(
                value
                for value in (
                    player.get("name") or self.search_term,
                    player.get("position"),
                    player.get("team"),
                )
                if value
            )
            self.players_combobox.addItem(label, player)

        if self.players_list:
            self.players_combobox.setCurrentIndex(1)
        self.players_combobox.blockSignals(False)

    def display_selected_player_info(self, index: int):
        player = self.players_combobox.itemData(index)
        if player:
            self.select_player(player)

    def select_player(self, player: dict):
        self.current_player = player
        self.update_player_header(player)
        self.populate_player_table(player)
        self.apply_player_colors(player)
        self.update_current_team_map(show_errors=False)
        self.display_player_image(self.player_image_search_term(player))

    def year_select(self):
        selected_year = self.years_combobox.currentText()
        if selected_year.isdigit():
            self.year = int(selected_year)

    def stat_select(self):
        selected_stat = self.stat_combobox.currentText()
        if selected_stat in STAT_CATEGORIES:
            self.stat_category = selected_stat

    def apply_player_colors(self, player: dict):
        primary = css_hex_color(first_value(player, "teamColor", "team_color"), DEFAULT_PRIMARY_COLOR)
        secondary = css_hex_color(
            first_value(player, "teamColorSecondary", "team_color_secondary"),
            DEFAULT_SECONDARY_COLOR,
        )
        self.apply_colors(primary, secondary)

    def apply_colors(self, primary: str, secondary: str):
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {primary};
                color: {secondary};
            }}
            QLabel#playerName {{
                font-size: 24px;
                font-weight: 700;
                padding: 8px;
                border-bottom: 2px solid {secondary};
            }}
            QLabel#teamName {{
                font-size: 15px;
                font-weight: 600;
                padding-bottom: 6px;
            }}
            QLabel#playerImage,
            QLabel#teamLogo {{
                background-color: rgba(255, 255, 255, 35);
                border: 2px solid {secondary};
                padding: 6px;
            }}
            QPushButton,
            QTextEdit,
            QComboBox,
            QTableWidget {{
                background-color: {primary};
                color: {secondary};
                border: 1px solid {secondary};
            }}
            QHeaderView::section {{
                background-color: {primary};
                color: {secondary};
                border: 1px solid {secondary};
            }}
            QPushButton {{
                font-weight: 700;
                padding: 6px;
            }}
            """
        )

    def update_player_header(self, player: dict):
        player_name = str(first_value(player, "name", default=self.search_term))
        team_name = str(first_value(player, "team", default=""))
        position = str(first_value(player, "position", default=""))

        self.player_name_label.setText(player_name)
        subtitle = " | ".join(value for value in (team_name, position) if value)
        self.team_name_label.setText(subtitle)

    def player_image_search_term(self, player: dict) -> str:
        team_name = str(first_value(player, "team", default=""))
        logo = self.team_repository.find_logo(team_name) if team_name else None
        return " ".join(
            str(value)
            for value in (
                first_value(player, "name", default=self.search_term),
                team_name,
                logo.mascot if logo else "",
                "football",
            )
            if value
        )

    def populate_player_table(self, player: dict):
        self.player_table.setRowCount(0)
        for key, value in player.items():
            row = self.player_table.rowCount()
            self.player_table.insertRow(row)
            self.player_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.player_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def current_team_name(self) -> Optional[str]:
        if not self.current_player:
            return None
        return self.current_player.get("team")

    def show_current_team(self):
        team_name = self.current_team_name()
        if not team_name:
            self.set_message("Select a player before checking a team.")
            return

        profile = self.team_repository.find_team_profile(team_name)
        if profile is None:
            self.set_message(f"No stadium coordinates found for {team_name}.")
            return

        self.set_message(
            f"{profile.stadium.team}: {profile.stadium.stadium} "
            f"({profile.stadium.latitude}, {profile.stadium.longitude})"
        )

    def update_current_team_map(self, checked=False, show_errors=True):
        team_name = self.current_team_name()
        if not team_name:
            if show_errors:
                self.set_message("Select a player before updating the map.")
            return

        profile = self.team_repository.find_team_profile(team_name)
        if profile is None:
            if show_errors:
                self.set_message(f"No stadium coordinates found for {team_name}.")
            self.team_logo_label.clear()
            return

        self.current_map = self.map_renderer.team_map(profile)
        self.display_map()
        self.display_team_logo(profile.logo)

    def random_game_update(self):
        team_name = self.current_team_name()
        player_name = (self.current_player or {}).get("name") or self.search_term
        if not team_name or not player_name:
            self.set_message("Select a player before requesting a random game.")
            return

        week = random.randint(1, 16)
        try:
            stats = self.football_client.player_week_stats(
                year=self.year,
                team=team_name,
                week=week,
                category=self.stat_category,
                player_name=player_name,
            )
        except Exception as error:
            self.set_message(f"Stats lookup failed: {user_facing_error(error)}")
            return

        self.populate_stats_table(stats)
        if not stats:
            self.set_message(f"No {self.stat_category} stats found for {player_name} in week {week}, {self.year}.")

    def populate_stats_table(self, stats: list[PlayerGameStat]):
        self.game_details_table.setRowCount(0)
        for stat in stats:
            row = self.game_details_table.rowCount()
            self.game_details_table.insertRow(row)
            self.game_details_table.setItem(row, 0, QTableWidgetItem(stat.player))
            self.game_details_table.setItem(row, 1, QTableWidgetItem(stat.stat_type))
            self.game_details_table.setItem(row, 2, QTableWidgetItem(stat.stat))
            self.game_details_table.setItem(row, 3, QTableWidgetItem(f"Week {stat.week}"))
            self.game_details_table.setItem(row, 4, QTableWidgetItem(str(stat.year)))

    def display_map(self):
        self.browser.setHtml(self.current_map._repr_html_())

    def display_player_image(self, search_term: str):
        image_url = self.image_service.first_valid_player_image_url(search_term)
        if image_url is None:
            self.image_label.clear()
            return

        pixmap = self.image_service.pixmap_from_url(image_url)
        if pixmap is None or pixmap.isNull():
            self.image_label.clear()
            return

        self.image_label.setPixmap(
            pixmap.scaled(168, 168, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def display_team_logo(self, logo: Optional[LogoInfo]):
        if not logo or not logo.logo_url:
            self.team_logo_label.clear()
            return

        pixmap = self.image_service.pixmap_from_url(logo.logo_url)
        if pixmap is None or pixmap.isNull():
            self.team_logo_label.clear()
            return

        self.team_logo_label.setPixmap(
            pixmap.scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def set_message(self, message: str):
        self.text_edit.setPlainText(message)

    def clear(self):
        self.search_term = ""
        self.players_list = []
        self.current_player = None
        self.text_edit.clear()
        self.players_combobox.clear()
        self.player_table.setRowCount(0)
        self.game_details_table.setRowCount(0)
        self.image_label.clear()
        self.team_logo_label.clear()
        self.player_name_label.setText("Search for a player")
        self.team_name_label.clear()
        self.current_map = self.map_renderer.default_map()
        self.apply_colors(DEFAULT_PRIMARY_COLOR, DEFAULT_SECONDARY_COLOR)
        self.display_map()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    player_search_app = PlayerSearchApp()
    player_search_app.show()
    sys.exit(app.exec_())
