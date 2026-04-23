from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import EmployeeDocument, Permission, TechnologyTag, UserProfile


class EmployeeProfileTestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        # Create normal user
        self.normal_user = User.objects.create_user(
            username="normal", email="normal@test.com", password="pass"
        )
        self.normal_profile, _ = UserProfile.objects.get_or_create(
            user=self.normal_user
        )

        # Create HR admin user
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_profile, _ = UserProfile.objects.get_or_create(user=self.hr_user)

        perm, _ = Permission.objects.get_or_create(
            module_name="Employee Profiles", feature_action="view_all_profiles"
        )
        self.hr_profile.add_permission(perm)
        # Refresh user cache
        self.hr_user.refresh_from_db()
        self.normal_user.refresh_from_db()

        self.other_user = User.objects.create_user(
            username="other", email="other@test.com", password="pass"
        )
        self.other_profile, _ = UserProfile.objects.get_or_create(user=self.other_user)

    def test_hr_can_list_all_profiles(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        res = self.client.get("/api/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Check if pagination is enabled
        data = res.json()
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        self.assertGreaterEqual(len(data), 2)

    def test_normal_user_list_only_own_profile(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)
        res = self.client.get("/api/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        if isinstance(data, dict) and "results" in data:
            data = data["results"]

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["email"], "normal@test.com")

    def test_hr_can_create_employee(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        data = {
            "email": "new.employee@test.com",
            "first_name": "New",
            "last_name": "Employee",
            "department": "Engineering",
        }
        res = self.client.post("/api/employees/", data)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.filter(email="new.employee@test.com").count(), 1)
        self.assertEqual(
            UserProfile.objects.filter(email_address="new.employee@test.com").count(), 1
        )

    def test_normal_user_cannot_create_employee(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)
        data = {"email": "hacker@test.com"}
        res = self.client.post("/api/employees/", data)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_soft_delete(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        res = self.client.delete(f"/api/employees/{self.normal_profile.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Verify soft delete
        self.normal_profile.refresh_from_db()
        self.assertFalse(self.normal_profile.is_active)
        self.assertEqual(
            self.normal_profile.employment_status, UserProfile.EmploymentStatus.INACTIVE
        )

    def test_hr_can_update_employee_tech_tags_from_static_ids(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)

        payload = {"tech_tags": [1, 6, 8]}
        res = self.client.patch(
            f"/api/employees/{self.normal_profile.id}/", payload, format="json"
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["tech_tags"], [1, 6, 8])

        self.normal_profile.refresh_from_db()
        self.assertEqual(
            list(
                self.normal_profile.tech_tags.order_by("name").values_list(
                    "name", flat=True
                )
            ),
            ["Node.js", "Python", "React"],
        )
        self.assertEqual(
            TechnologyTag.objects.filter(
                name__in=["React", "Python", "Node.js"]
            ).count(),
            3,
        )

    def test_invalid_tech_tag_id_returns_validation_error(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)

        res = self.client.patch(
            f"/api/employees/{self.normal_profile.id}/",
            {"tech_tags": [1, 999]},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tech_tags", res.data)

    def test_owner_can_upload_cv_file_and_latest_is_current(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        first_file = SimpleUploadedFile(
            "cv-v1.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        second_file = SimpleUploadedFile(
            "cv-v2.pdf", b"%PDF-1.4 fake2", content_type="application/pdf"
        )

        res1 = self.client.post(
            f"/api/employees/{self.normal_profile.id}/cvs/",
            {"file": first_file},
            format="multipart",
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res1.data["is_current"])
        self.assertEqual(res1.data["source_type"], "file")

        res2 = self.client.post(
            f"/api/employees/{self.normal_profile.id}/cvs/",
            {"file": second_file},
            format="multipart",
        )
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res2.data["is_current"])

        docs = list(
            EmployeeDocument.objects.filter(
                user_profile=self.normal_profile, doc_type="cv"
            ).order_by("-uploaded_at")
        )
        self.assertEqual(len(docs), 2)
        self.assertTrue(docs[0].is_current)
        self.assertFalse(docs[1].is_current)

    def test_owner_can_create_external_cv(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        payload = {
            "source_type": "external_link",
            "provider": "canva",
            "external_url": "https://www.canva.com/design/DA123456789/view",
            "file_name": "Canva CV",
        }
        res = self.client.post(
            f"/api/employees/{self.normal_profile.id}/cvs/",
            payload,
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["source_type"], "external_link")
        self.assertEqual(res.data["provider"], "canva")
        self.assertEqual(res.data["external_url"], payload["external_url"])
        self.assertIsNone(res.data["file_key"])

    def test_non_owner_non_hr_cannot_create_cv(self):
        other_user = User.objects.get(id=self.other_user.id)
        self.client.force_authenticate(user=other_user)
        file_obj = SimpleUploadedFile(
            "cv.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        res = self.client.post(
            f"/api/employees/{self.normal_profile.id}/cvs/",
            {"file": file_obj},
            format="multipart",
        )
        self.assertIn(
            res.status_code, {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}
        )

    def test_cv_download_returns_external_url_for_external_record(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        create_res = self.client.post(
            f"/api/employees/{self.normal_profile.id}/cvs/",
            {
                "source_type": "external_link",
                "provider": "other",
                "external_url": "https://example.com/my-cv",
            },
            format="json",
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        cv_id = create_res.data["id"]

        download_res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/cvs/{cv_id}/download/"
        )
        self.assertEqual(download_res.status_code, status.HTTP_200_OK)
        self.assertEqual(download_res.data, {"url": "https://example.com/my-cv"})

    def test_profile_modal_bundle_returns_all_sections_for_owner(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        detail = self.client.get(f"/api/employees/{self.normal_profile.id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

        res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("employee", res.data)
        self.assertIn("cv_versions", res.data)
        self.assertIn("lookups", res.data)
        self.assertIn("cpf_levels_for_role", res.data)

        self.assertEqual(res.data["employee"]["email"], detail.data["email"])
        lookups = res.data["lookups"]
        self.assertIn("departments", lookups)
        self.assertIn("roles", lookups)
        self.assertIn("projects", lookups)
        self.assertIn("managers", lookups)
        self.assertIsInstance(lookups["departments"], list)
        self.assertIsInstance(lookups["roles"], list)
        self.assertTrue("ETag" in res.headers or "Etag" in res.headers)

    def test_profile_modal_bundle_sections_filters_payload(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/",
            {"sections": "employee"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(set(res.data.keys()), {"employee"})

    def test_profile_modal_bundle_invalid_sections_returns_400(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/",
            {"sections": "employee,extra_bit"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_profile_modal_bundle_hr_can_access_other_profile(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)

        res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["employee"]["email"], "normal@test.com")

    def test_profile_modal_bundle_non_owner_denied_or_not_found(self):
        other_user = User.objects.get(id=self.other_user.id)
        self.client.force_authenticate(user=other_user)

        res = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/"
        )
        self.assertIn(
            res.status_code,
            {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND},
        )

    def test_profile_modal_bundle_if_none_match_returns_304(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        first = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/",
            {"sections": "employee"},
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        etag = first.headers.get("ETag") or first.headers.get("Etag")
        self.assertIsNotNone(etag)

        second = self.client.get(
            f"/api/employees/{self.normal_profile.id}/profile-modal-bundle/",
            {"sections": "employee"},
            HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(second.status_code, status.HTTP_304_NOT_MODIFIED)

    def test_profile_page_bundle_hr_returns_employees_and_lookups(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)

        res = self.client.get("/api/employees/profile-page-bundle/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.assertIsInstance(res.data["permissions"], int)
        self.assertEqual(res.data["permissions"], res.data["permissions_bitmap"])
        self.assertIn("results", res.data["employees"])
        self.assertIn("count", res.data["employees"])
        self.assertGreaterEqual(res.data["employees"]["count"], 2)

        lookups = res.data["lookups"]
        for key in ("departments", "roles", "projects", "managers", "cpf_levels"):
            self.assertIn(key, lookups)

    def test_profile_page_bundle_normal_user_sees_only_self(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)

        res = self.client.get("/api/employees/profile-page-bundle/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["employees"]["count"], 1)
        self.assertEqual(
            res.data["employees"]["results"][0]["email"], "normal@test.com"
        )
