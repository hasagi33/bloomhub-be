import pytest
from django.contrib.auth.models import User
from django.test import Client
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.mark.django_db
def test_swagger_endpoints():
    """Test that swagger endpoints are accessible"""
    client = Client()

    print("Testing Swagger endpoints...")

    # Test schema endpoint
    response = client.get("/api/schema/")
    print(f"Schema endpoint (/api/schema/): {response.status_code}")
    assert response.status_code == 200

    # Test swagger UI
    response = client.get("/api/schema/swagger-ui/")
    print(f"Swagger UI (/api/schema/swagger-ui/): {response.status_code}")
    assert response.status_code == 200

    # Test redoc
    response = client.get("/api/schema/redoc/")
    print(f"ReDoc (/api/schema/redoc/): {response.status_code}")
    assert response.status_code == 200


@pytest.mark.django_db
def test_asset_endpoints():
    """Test that asset management endpoints are accessible"""
    client = Client()

    # Create a test user and get JWT token
    user = User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
        is_staff=True,
    )

    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)

    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}

    print("\nTesting Asset Management endpoints...")

    # Test asset endpoints
    endpoints = [
        "/api/assets/",
        "/api/assignments/",
        "/api/replacement-logs/",
        "/api/user-profiles/",
    ]

    for endpoint in endpoints:
        response = client.get(endpoint, **headers)
        print(f"{endpoint}: {response.status_code}")
        assert response.status_code == 200

    # Clean up
    user.delete()
