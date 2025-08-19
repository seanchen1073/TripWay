import os
from decimal import Decimal
import requests
from django.core.management.base import BaseCommand
from spots.models import Spot
from dotenv import load_dotenv

load_dotenv()

class Command(BaseCommand):
    help = "填寫 spot 初始資料"

    def handle(self, *args, **options):
        print("正在執行你的腳本...")
        api_key = os.getenv("GOOGLE_API_KEY")
        
        # 新版 Places API 端點
        url = "https://places.googleapis.com/v1/places:searchNearby"
        
        headers = {
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.priceLevel,places.websiteUri,places.nationalPhoneNumber'
        }
        
        data = {
            "includedTypes": ["tourist_attraction"],
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": 25.0330,
                        "longitude": 121.5654
                    },
                    "radius": 50000.0
                }
            }
        }
        
        response = requests.post(url, json=data, headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            places = result.get("places", [])
            print(f"Found {len(places)} places")
            
            for place in places:
                name = place.get("displayName", {}).get("text", "")
                if name:
                    Spot.objects.get_or_create(
                        name=name,
                        defaults={
                            "address": place.get("formattedAddress"),
                            "latitude": Decimal(str(place.get("location", {}).get("latitude", 0))),
                            "longitude": Decimal(str(place.get("location", {}).get("longitude", 0))),
                            "rating": place.get("rating"),
                            "url": place.get("websiteUri"),
                            "phone": place.get("nationalPhoneNumber"),
                            "place_id": place.get("id"),
                        },
                    )
        else:
            print(f"API 錯誤: {response.status_code}")
            print(f"回應: {response.text}")
        
        print("腳本執行完畢")