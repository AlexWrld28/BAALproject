import sys
import csv
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QTableWidget, \
    QTableWidgetItem, QComboBox
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtGui import QPixmap
import cfbd
from cfbd.models import team
from cfbd.api.players_api import PlayersApi
from cfbd.rest import ApiException
import folium
import requests
import random
from googlesearch import search
from PIL import Image
import base64
from bs4 import BeautifulSoup
from io import BytesIO
import arcgis
from arcgis.gis import GIS
from arcgis.mapping import WebMap

class PlayerSearchApp(QWidget):
    def __init__(self):
        super().__init__()

        # Access key
        self.configuration = cfbd.Configuration()
        self.configuration.api_key['Authorization'] = 'EnktQfRtiDelCyKj3HMoxXWZelUAkLmfxPC1HSwA6UcOKQEw2XjPb81HzSx8HG2A'
        self.configuration.api_key_prefix['Authorization'] = 'Bearer'
        # Saves Access key to the PlayerSearchApp object for multi scope use
        self.api_instance = cfbd.PlayersApi(cfbd.ApiClient(self.configuration))
        self.players_list = []  # List to store players when multiple are returned
        # Initializes the currently non function get team button
        self.check_team_name = QPushButton('Get team')
        self.check_team_name.clicked.connect(lambda: self.get_team_coordinates(self.extract_team_name(self.current_response)))

        # Initializes the statistic category for the player random game function, also used in multiple different scopes
        self.stat_cat = 'passing'
        self.stat_combobox = QComboBox(self)
        self.stat_combobox.addItem("Stat Type")
        self.stat_combobox.addItem("rushing")
        self.stat_combobox.addItem("passing")
        self.stat_combobox.addItem("defensive")
        self.stat_combobox.addItem('receiving')
        self.stat_combobox.addItem('special teams')
        self.stat_combobox.addItem('blocking')
        self.stat_combobox.addItem('offensive')
        self.stat_combobox.addItem('defense')
        self.stat_combobox.currentIndexChanged.connect(self.stat_select)
        
        # Establishes a default year for the player random game lookup, as well as the combobox for manually editing this year
        self.year = 2023
        self.years_combobox = QComboBox(self)
        self.years_combobox.currentIndexChanged.connect(self.year_select)
        self.years_combobox.addItem("Select Year: ")
        for self.year in range(2022, 1899, -1): # Iterate over all the past years up until 1900
            self.years_combobox.addItem(str(self.year))

        # Create a map centered at a default location
        self.map = folium.Map(location=[34.0522, -118.2437], zoom_start=12)

        # Add a circle marker with a popup
        folium.CircleMarker([34.0522, -118.2437], radius=10, color='red', fill=True, fill_color='red', popup="Los Angeles").add_to(self.map)

        self.init_ui()

        # Display the map in the GUI
        self.display_map()


    def init_ui(self):
        self.setWindowTitle('Player Search')
        self.setGeometry(100, 100, 2400, 1800)  # Adjust the dimensions of the main window

        self.label = QLabel('Enter Player Name:', self)
        self.text_edit = QTextEdit(self)
        self.text_edit.setFixedHeight(30)  # Set the fixed height for the search bar

        self.button = QPushButton('Search', self)
        self.button.clicked.connect(self.search_player)

        self.players_combobox = QComboBox(self)
        self.players_combobox.currentIndexChanged.connect(self.display_selected_player_info)
        

        self.table_widget = QTableWidget(self)
        self.table_widget.setColumnCount(2)  # Assuming you have key-value pairs in the response
        self.table_widget.setHorizontalHeaderLabels(['Attribute', 'Value'])

        self.game_details_table = QTableWidget(self)
        self.game_details_table.setColumnCount(5)  # Assuming you have key-value pairs in the response
        self.game_details_table.setHorizontalHeaderLabels(['Name', 'Stat Type', 'Stat', 'Week', 'Year'])

        self.image_label = QLabel(self)

        # Use a QHBoxLayout for side-by-side arrangement
        main_layout = QHBoxLayout(self)

        # Left side layout for player info
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.label)
        left_layout.addWidget(self.text_edit)
        left_layout.addWidget(self.button)
        left_layout.addWidget(self.check_team_name)
        left_layout.addWidget(self.players_combobox)
        left_layout.addWidget(self.image_label)
        left_layout.addWidget(self.table_widget)

        main_layout.addLayout(left_layout)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.label)
        left_layout.addWidget(self.text_edit)
        left_layout.addWidget(self.button)
        left_layout.addWidget(self.years_combobox)
        left_layout.addWidget(self.players_combobox)
        left_layout.addWidget(self.image_label)
        left_layout.addWidget(self.table_widget)
        left_layout.addWidget(self.game_details_table)  # Add the game details table below the existing table
        left_layout.addWidget(self.check_team_name)
        left_layout.addWidget(self.stat_combobox)

        main_layout.addLayout(left_layout)


        # Button to clear the UI
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.clicked.connect(self.clear_gui_colors)
        self.clear_button.clicked.connect(self.clear)
        left_layout.addWidget(self.clear_button)
        
        # Create a button for calling rand_game_update
        self.rand_game_button = QPushButton("Random Game")
        self.rand_game_button.clicked.connect(lambda: self.rand_game_update(self.extract_team_name(self.current_response)))
        left_layout.addWidget(self.rand_game_button)
        
        main_layout.addLayout(left_layout)

        # Right side layout for the map
        right_layout = QVBoxLayout()

        # Button to update the map
        self.update_button = QPushButton("Update Map")
        self.update_button.clicked.connect(lambda: self.update_map(self.extract_team_name(self.current_response)))
        right_layout.addWidget(self.update_button)

        # Web view to display the map
        self.browser = QWebEngineView()
        right_layout.addWidget(self.browser)

        main_layout.addLayout(right_layout)

        self.setLayout(main_layout)
        self.setWindowTitle("Folium Map in PyQt5")
        self.setGeometry(100, 100, 1100, 1200)  # Adjust the dimensions again to ensure consistency


    def search_player(self):
        self.search_term = self.text_edit.toPlainText()

        try:
            # Fetch player information
            response = self.api_instance.player_search(search_term=self.search_term)

            # Print the entire response for debugging
            print("Full Response:", response)

            # Clear existing table content
            self.table_widget.setRowCount(0)

            if isinstance(response, list) and response:
                # Collect positions from all players
                positions = set()
                for item in response:
                    response_dict = item.to_dict()
                    self.players_list.append(response_dict)
                    position = response_dict.get('position', '')
                    if position:
                        positions.add(position)

                # Clear existing combo box content
                self.players_combobox.clear()

                # Add unique positions to the combo box
                self.players_combobox.addItem("Select Player")
                self.players_combobox.addItems(positions)

                # Display information for the first matching player if it's a single player response
                if len(response) == 1:
                    self.process_response(response[0].to_dict())

                # Fetch and display player image for the first player
                image_url = self.fetch_player_image(self.search_term)
                if image_url:
                    self.display_player_image(image_url)
                    

            elif isinstance(response, cfbd.PlayerSearchResult):
                # For a single player response
                response_dict = response.to_dict()
                self.process_response(response_dict)

                # Fetch and display player image for the single player
                image_url = self.fetch_player_image(self.search_term)
                if image_url:
                    self.display_player_image(image_url)

            else:
                error_message = "Invalid response format"
                self.text_edit.setPlainText(error_message)

        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            self.text_edit.setPlainText(error_message)
        
        self.current_response = response

    def year_select(self):
        # Retrieve the selected year from the combobox
        selected_year = self.years_combobox.currentText()

        # Check if the selected year is not the placeholder value
        if selected_year.isdigit():
            # Update self.year with the selected year
            self.year = int(selected_year)
            print(self.year)

    # For single player responses
    def process_response(self, response_dict):
        team_color = response_dict.get('team_color', '')
        secondary_team_color = response_dict.get('team_color_secondary', '')
        # Update GUI colors
        self.update_gui_colors(team_color, secondary_team_color)
        self.populate_table(response_dict)

    def stat_select(self):
        selected_stat = self.stat_combobox.currentText()

        if selected_stat != 'Stat Type':
            self.stat_cat = selected_stat
            print(self.stat_cat)
        else:
            print("please select a stat type")    
    def update_gui_colors(self, team_color, secondary_team_color):

        # Update GUI colors
        self.setStyleSheet(f"background-color: {team_color};")
        self.label.setStyleSheet(f"color: {secondary_team_color};")
        self.button.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.text_edit.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.table_widget.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.update_button.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.game_details_table.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.rand_game_button.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.clear_button.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.check_team_name.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")
        self.stat_combobox.setStyleSheet(f"background-color: {team_color}; color: {secondary_team_color};")

    def populate_table(self, response_dict, team_color=None, secondary_team_color=None):
        team_color = response_dict.get('team_color', '')
        secondary_team_color = response_dict.get('team_color_secondary', '')

        # Update GUI colors
        self.update_gui_colors(team_color, secondary_team_color)

        # Clear existing table content
        self.table_widget.setRowCount(0)

        # Populate the table with response data
        for key, value in response_dict.items():
            row_position = self.table_widget.rowCount()
            self.table_widget.insertRow(row_position)
            self.table_widget.setItem(row_position, 0, QTableWidgetItem(str(key)))
            self.table_widget.setItem(row_position, 1, QTableWidgetItem(str(value)))

    def display_selected_player_info(self, index):
        if 0 <= index < len(self.players_list):
            selected_position = self.players_combobox.currentText()

            if selected_position == "Select Player":
                return

            matching_players = [player for player in self.players_list if player.get('position') == selected_position]

            # Display the information for the first matching player
            if matching_players:
                response_dict = matching_players[0]
                team_color = response_dict.get('team_color', '')
                secondary_team_color = response_dict.get('team_color_secondary', '')
                self.populate_table(response_dict, team_color, secondary_team_color)

                # Get the team name from the selected player
                team_name = response_dict.get('team', '').lower()

                # Call get_team_coordinates to update coordinates
                coordinates = self.get_team_coordinates(team_name)

                # Check if coordinates are valid and update the map
                if coordinates is not None and all(isinstance(coord, (float, int)) for coord in coordinates):
                    self.update_map(team_name)



    def extract_team_name(self, response):
        if response:
            if isinstance(response, list):
                # Assuming team name is part of the player response
                team_name = response[0].to_dict().get('team', '')
                return team_name
            elif isinstance(response, cfbd.PlayerSearchResult):
                # For a single player response
                team_name = response.to_dict().get('team', '')
                team_name = self.team_name
            else:
                team_name = None

            
        else:
            return None

    def get_team_coordinates(self, team_name):
        # File path
        csv_file_path = r'stadiums-geocoded.csv'

        try:
            if team_name:
                with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
                    csv_reader = csv.DictReader(csvfile)
                    for row in csv_reader:
                        if row.get('team', '').lower() == team_name.lower():
                            latitude = row.get('latitude', '')
                            longitude = row.get('longitude', '')
                            print(f"Coordinates found for team {team_name}: Latitude={latitude}, Longitude={longitude}")
                            if latitude and longitude:
                                return float(latitude), float(longitude)

                # If the team is not found in the CSV, return None
                print(f"Coordinates not found for team: {team_name}")
                return None, None
            else:
                print("Invalid team name")
                return None, None

        except Exception as e:
            print(f"An error occurred while fetching team coordinates: {str(e)}")
            return None, None
    
    def update_map(self, team_name):
        # Print or log the team name for debugging
        print(f"Team Name: {team_name}")

        # Obtain team coordinates from the method
        coordinates = self.get_team_coordinates(team_name)

        # Check if coordinates are valid
        if coordinates is not None and all(isinstance(coord, (float, int)) for coord in coordinates):

            latitude, longitude = coordinates

            # Open the CSV file
            with open("logos\logos.csv", 'r') as logos:
                csv_reader = csv.DictReader(logos)
                for row in csv_reader:
                    # Extract team name and logo from the current row
                    alt_name = row['school']
                    # Cheacks if the team name matches the pre-existing name
                    if team_name == alt_name:

                        team_logo_url = row['logo']
                        team_abbrv = row['abbreviation']
                        team_conf = row['conference']
                        team_div = row['division']
                        # Download the image
                        response = requests.get(team_logo_url)
                        image = Image.open(BytesIO(response.content))
                        # Assuming you want a maximum width of 200 pixels
                        max_width = 100

                        # Calculate the aspect ratio
                        aspect_ratio = image.width / image.height

                        # Calculate the new width and height
                        new_width = min(image.width, max_width)
                        new_height = int(new_width / aspect_ratio)

                        # Resize the image
                        resized_image = image.resize((new_height, new_width))

                        # Convert the resized image to bytes
                        buffered = BytesIO()
                        resized_image.save(buffered, format="PNG")  # Change format if needed
                        image_bytes = buffered.getvalue()
                        image_str = base64.b64encode(image_bytes).decode()

                        with open("stadiums-geocoded.csv", 'r') as stadDetails:
                            csv_reader = csv.DictReader(stadDetails)
                            for row in csv_reader:
                                    school = row['team']
                                    if school == team_name:
                                        # Extract the rest of the stadium details from the stadiums-geocoded file
                                        stadium_name = row['stadium']
                                        self.stadium = stadium_name
                                        capacity = row['capacity']
                                        year_built = row['built']
                                        last_expanded = row['expanded']    
                                        # Create a marker with a popup containing the logo image
                                        popup_html = f'<img src="data:image/png;base64,{image_str}" alt="{team_name}_logo">'
                                        popup_html += f'<br>Team Abbreviation: {team_abbrv}'
                                        popup_html += f'<br>Team Conference: {team_conf}'
                                        popup_html += f'<br>Team Division: <br>{team_div}'
                                        popup_html += f'<br>Stadium Name: <br>{stadium_name}'
                                        popup_html += f'<br>Capacity: <br>{capacity}'
                                        popup_html += f'<br>Year Built: <br>{year_built}'
                                        popup_html += f'<br>Last Expanded: <br>{last_expanded}'
                                        popup = folium.Popup(popup_html, show=True)
                                        marker = folium.Marker(location=[latitude, longitude], popup=popup)
                                        self.map = folium.Map(location=[latitude, longitude], zoom_start=16)

                                        # Add the marker to the map
                                        marker.add_to(self.map)
                                        # Update and display the map in the GUI
                                        self.display_map()
                               
        else:
            print(f"Invalid coordinates for team: {team_name}")
            
    def rand_game_update(self, team_name):

        # Search parameters
        api_instance = cfbd.PlayersApi(cfbd.ApiClient(self.configuration))
        year = self.year  # int | Year filter
        team = team_name  # str | Team filter (optional)
        start_week = random.randint(1, 16)  # int | Start week filter (optional)
        end_week = start_week  # int | End week filter (optional)
        self.week = f'Week {end_week}'
        category = self.stat_cat  # str | Stat category filter (e.g. passing) (optional)

        try:
            # Fetch data from the API
            api_response = api_instance.get_player_season_stats(year, team=team, start_week=start_week, end_week=end_week, category=category)
            
            filtered_dicts = [api_response]

            # Iterate over each object in the list
            for obj in api_response:
                # Check if the object has a 'player' attribute and if it matches the search term
                if hasattr(obj, 'player') and obj.player.lower() == self.search_term.lower():
                    # If so, append the object to the list of matching objects
                    filtered_dicts.append(obj)
            # Print the matching objects
            for match in filtered_dicts:
                print(match)

            # Populate the table with the API response data
            self.populate_combo_box_table(filtered_dicts)

        except ApiException as e:
            print("Exception when calling PlayersApi->get_player_season_stats: %s\n" % e)

    def populate_combo_box_table(self, filtered_dicts):
        # Clear existing contents of the table
        self.game_details_table.setRowCount(0)
        
        # Iterate over each game_details instance in the list
        for game_details in filtered_dicts:
            # Get the current row count
            row_count = self.game_details_table.rowCount()

            # Add a new row to the table for each game_details entry
            self.game_details_table.insertRow(row_count)

            # Check if 'player', 'stat_type', 'stat', and 'week' attributes are present and not empty in the game_details instance
            if hasattr(game_details, 'player') and hasattr(game_details, 'stat_type') and hasattr(game_details, 'stat'):
                    # Set 'player' as the first column
                    self.game_details_table.setItem(row_count, 0, QTableWidgetItem(game_details.player))

                    # Set 'stat_type' as the attribute in the second column
                    self.game_details_table.setItem(row_count, 1, QTableWidgetItem(game_details.stat_type))

                    # Set 'stat' as the value in the third column
                    self.game_details_table.setItem(row_count, 2, QTableWidgetItem(str(game_details.stat)))

                    # Set 'week' as the fourth column
                    self.game_details_table.setItem(row_count, 3, QTableWidgetItem(str(self.week)))

                    # Set year from the previous method as the value of this column, labeled 'Year'
                    self.game_details_table.setItem(row_count, 4, QTableWidgetItem(str(self.year)))

    

    def display_map(self):
        # Get the HTML representation of the map
        html_map = self.map._repr_html_()
        
        # Set HTML content to the QWebEngineView
        self.browser.setHtml(html_map)


    def fetch_player_image(self, search_term):
        try:
            # Perform Google Images search to get the player image URL
            query = f"{search_term} player"
            for j in search(query, num=15, stop=15, pause= 1):
                print(f"Trying to fetch image from URL: {j}")
                image_data = requests.get(j).content
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                if not pixmap.isNull():
                    print("Image loaded successfully")
                    return j  # Return the URL of the first valid image
                
            # If no valid image is found, return None
            print("No valid image found")
            return None

        except Exception as e:
            print(f"An error occurred while fetching player image: {str(e)}")
            return None

    def display_player_image(self, image_url):
        try:
            print(f"Displaying image from URL: {image_url}")
            # Fetch player image and display it
            image_data = requests.get(image_url).content
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)

            # Check if the pixmap is null
            if not pixmap.isNull():
                print("Image loaded successfully")
                # Resize the pixmap by 50%
                width = int(pixmap.width() * 0.1)
                height = int(pixmap.height() * 0.1)
                pixmap = pixmap.scaled(width, height)
                self.image_label.setPixmap(pixmap)
            else:
                # Handle the case where the pixmap is null (e.g., invalid image data)
                print("Invalid image data")
                error_message = "Invalid image data or failed to load image"
                placeholder_image_path = 'Placeholder.jpg'
                self.image_label.setPixmap(QPixmap(placeholder_image_path))
                # Alternatively, you can clear the label:
                # self.image_label.clear()

        except Exception as e:
            print(f"An error occurred while displaying player image: {str(e)}")
            # Handle the error in a way that makes sense for your application
            error_message = "An error occurred while displaying player image"
            print(error_message)
            placeholder_image_path = 'Placeholder.jpg'
            self.image_label.setPixmap(QPixmap(placeholder_image_path))
            # Alternatively, you can clear the label:
            # self.image_label.clear()

    def clear(self):
        # Clear the search text edit
        self.text_edit.clear()

        # Clear the players combo box
        self.players_combobox.clear()

        # Clear the table widget
        self.table_widget.clearContents()

        # Clear the image label
        self.image_label.clear()

        # Clear the game stat table
        self.game_details_table.clear()

        # Clear the player information table
        self.table_widget.clear()

        # Run the update gui colors method one more time, but with #ffffff (white) as the team_color and #000000 (black) as the secondary_team_color
        team_color = '#ffffff'
        secondary_team_color = '#000000'
        self.update_gui_colors(team_color, secondary_team_color)

        # Reset the map to the initial state
        self.map = folium.Map(location=[34.0522, -118.2437], zoom_start=12)
        self.display_map()

        
    def clear_gui_colors(self):
        # Reset GUI colors to white
        self.setStyleSheet("background-color: white;")
        self.label.setStyleSheet("color: black;")
        self.button.setStyleSheet("background-color: white; color: black;")
        self.text_edit.setStyleSheet("background-color: white; color: black;")
        self.table_widget.setStyleSheet("background-color: white; color: black;")
        self.update_button.setStyleSheet("background-color: white; color: black;")
        self.game_details_table.setStyleSheet("background-color: white; color: black;")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    player_search_app = PlayerSearchApp()
    player_search_app.show()
    sys.exit(app.exec_())