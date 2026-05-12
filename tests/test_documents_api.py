from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    Document,
    DocumentSignatureAuditLog,
    DocumentSigner,
    Permission,
    Role,
)


class DocumentsAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.employee_role = Role.objects.create(name="employee")
        self.hr_role = Role.objects.create(name="hr")
        self.admin_role = Role.objects.create(name="admin")

        self.employee_user = User.objects.create_user(
            username="employee_user",
            email="employee@example.com",
            password="pass123",
        )
        self.hr_user = User.objects.create_user(
            username="hr_user",
            email="hr@example.com",
            password="pass123",
        )
        self.admin_user = User.objects.create_user(
            username="admin_user",
            email="admin@example.com",
            password="pass123",
        )

        self.employee_profile = self.employee_user.profile
        self.hr_profile = self.hr_user.profile
        self.admin_profile = self.admin_user.profile

        self.employee_profile.role = self.employee_role
        self.hr_profile.role = self.hr_role
        self.admin_profile.role = self.admin_role
        self.employee_profile.save(update_fields=["role"])
        self.hr_profile.save(update_fields=["role"])
        self.admin_profile.save(update_fields=["role"])

        self.employee_doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/employee-contract.pdf",
            name="Employee Contract",
            description="Signed contract",
            original_filename="contract.pdf",
            file_size=123,
            mime_type="application/pdf",
            signature_status=Document.SignatureStatus.SIGNED,
            is_confidential=False,
            tags=["contract"],
            allowed_roles=[Document.AccessRole.EMPLOYEE],
            expiry_date=date(2030, 1, 1),
        )
        self.hr_doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.POLICIES,
            file_key="documents/hr-policy.pdf",
            name="HR Policy",
            description="HR only",
            original_filename="hr-policy.pdf",
            file_size=456,
            mime_type="application/pdf",
            signature_status=Document.SignatureStatus.NOT_REQUIRED,
            is_confidential=False,
            tags=["hr"],
            allowed_roles=[Document.AccessRole.HR],
        )
        self.confidential_doc = Document.objects.create(
            uploaded_by=self.admin_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/confidential.pdf",
            name="Confidential",
            description="Do not expose",
            original_filename="confidential.pdf",
            file_size=789,
            mime_type="application/pdf",
            signature_status=Document.SignatureStatus.PENDING,
            is_confidential=True,
            tags=["secret"],
            allowed_roles=[Document.AccessRole.ADMIN],
        )

    # ── list / access control ──────────────────────────────────────────

    def test_employee_list_only_employee_documents(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertEqual(ids, {self.employee_doc.id})

    def test_hr_list_includes_hr_and_employee_excludes_confidential(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.employee_doc.id, ids)
        self.assertIn(self.hr_doc.id, ids)
        self.assertNotIn(self.confidential_doc.id, ids)

    def test_admin_list_includes_all_documents(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertEqual(
            ids, {self.employee_doc.id, self.hr_doc.id, self.confidential_doc.id}
        )

    # ── filters ───────────────────────────────────────────────────────

    def test_category_filter(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/documents/?category=policies")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["category"], Document.Category.POLICIES
        )

    def test_category_filter_invalid_returns_400(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/documents/?category=invalid")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_signature_status_filter(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(
            f"/api/documents/?signature_status={Document.SignatureStatus.SIGNED}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.employee_doc.id, ids)
        self.assertNotIn(self.hr_doc.id, ids)

    def test_expiry_filter_expiring_soon(self):
        self.client.force_authenticate(user=self.admin_user)
        soon_doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/soon.pdf",
            name="Expiring Soon Doc",
            original_filename="soon.pdf",
            expiry_date=date.today() + timedelta(days=5),
            allowed_roles=[Document.AccessRole.ADMIN],
        )
        response = self.client.get("/api/documents/?expiry_filter=expiring_soon")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(soon_doc.id, ids)

    def test_search_filter(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/documents/?search=HR+Policy")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.hr_doc.id)

    # ── upload ────────────────────────────────────────────────────────

    def test_upload_document_success(self):
        self.client.force_authenticate(user=self.hr_user)
        upload = SimpleUploadedFile(
            "new-policy.pdf",
            b"%PDF-1.4 test",
            content_type="application/pdf",
        )
        response = self.client.post(
            "/api/documents/",
            {
                "file": upload,
                "name": "New Policy",
                "category": Document.Category.POLICIES,
                "description": "Policy docs",
                "is_confidential": "false",
                "tags": ["policy", "general"],
                "allowed_roles": [Document.AccessRole.EMPLOYEE, Document.AccessRole.HR],
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "New Policy")
        self.assertEqual(response.data["category"], Document.Category.POLICIES)
        self.assertEqual(
            set(response.data["allowed_roles"]),
            {Document.AccessRole.EMPLOYEE, Document.AccessRole.HR},
        )
        self.assertEqual(response.data["file_name"], "new-policy.pdf")
        self.assertEqual(response.data["current_version"], "1.0")
        self.assertEqual(response.data["version_count"], 1)

    def test_upload_rejects_invalid_allowed_roles(self):
        self.client.force_authenticate(user=self.hr_user)
        upload = SimpleUploadedFile(
            "invalid.pdf",
            b"%PDF-1.4 test",
            content_type="application/pdf",
        )
        response = self.client.post(
            "/api/documents/",
            {
                "file": upload,
                "name": "Invalid Roles",
                "category": Document.Category.CONTRACTS,
                "allowed_roles": ["intern"],
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("allowed_roles", response.data)

    def test_upload_rejects_oversized_file(self):
        self.client.force_authenticate(user=self.hr_user)
        big_file = SimpleUploadedFile(
            "big.pdf",
            b"x" * (26 * 1024 * 1024),
            content_type="application/pdf",
        )
        response = self.client.post(
            "/api/documents/",
            {"file": big_file, "name": "Big", "category": Document.Category.OTHER},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_unsupported_mime(self):
        self.client.force_authenticate(user=self.hr_user)
        bad_file = SimpleUploadedFile(
            "script.sh", b"#!/bin/bash", content_type="text/x-sh"
        )
        response = self.client.post(
            "/api/documents/",
            {"file": bad_file, "name": "Script", "category": Document.Category.OTHER},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── retrieve ──────────────────────────────────────────────────────

    def test_retrieve_accessible_document(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get(f"/api/documents/{self.employee_doc.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.employee_doc.id)
        self.assertIn("signers", response.data)
        self.assertIn("version_count", response.data)

    def test_retrieve_denied_when_no_access(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get(f"/api/documents/{self.hr_doc.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── download ──────────────────────────────────────────────────────

    def test_download_allowed_for_accessible_doc(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get(f"/api/documents/{self.employee_doc.id}/download/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("signed_url", response.data)

    def test_download_denied_when_no_access(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get(f"/api/documents/{self.hr_doc.id}/download/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", response.data)

    def test_download_denied_for_confidential_non_admin(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(
            f"/api/documents/{self.confidential_doc.id}/download/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_download_allowed_for_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            f"/api/documents/{self.confidential_doc.id}/download/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("signed_url", response.data)

    # ── delete ────────────────────────────────────────────────────────

    def test_delete_by_admin_succeeds(self):
        self.client.force_authenticate(user=self.admin_user)
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/to-delete.pdf",
            name="To Delete",
            original_filename="to-delete.pdf",
            allowed_roles=[Document.AccessRole.ADMIN],
        )
        response = self.client.delete(f"/api/documents/{doc.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk=doc.id).exists())

    def test_delete_by_employee_denied(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.delete(f"/api/documents/{self.employee_doc.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── archive ───────────────────────────────────────────────────────

    def test_archive_by_hr_succeeds(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.hr_doc.refresh_from_db()
        self.assertTrue(self.hr_doc.archived)

    def test_archive_idempotent(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_archived_docs_hidden_from_default_list(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        response = self.client.get("/api/documents/")
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(self.hr_doc.id, ids)

    def test_archived_docs_visible_with_flag(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        response = self.client.get("/api/documents/?archived=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.hr_doc.id, ids)

    def test_unarchive_by_hr_succeeds(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/unarchive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.hr_doc.refresh_from_db()
        self.assertFalse(self.hr_doc.archived)
        list_response = self.client.get("/api/documents/")
        ids = {row["id"] for row in list_response.data["results"]}
        self.assertIn(self.hr_doc.id, ids)

    def test_unarchive_idempotent_when_not_archived(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/unarchive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.hr_doc.refresh_from_db()
        self.assertFalse(self.hr_doc.archived)

    def test_unarchive_by_employee_denied(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(f"/api/documents/{self.hr_doc.id}/archive/")
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/unarchive/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_preview_pdf_with_empty_mime_succeeds(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/blank.pdf",
            name="Blank",
            original_filename="blank.pdf",
            file_size=10,
            mime_type="",
            signature_status=Document.SignatureStatus.NOT_REQUIRED,
            is_confidential=False,
            tags=[],
            allowed_roles=[Document.AccessRole.HR],
        )
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("preview_url", response.data)
        self.assertTrue(response.data["preview_url"])

    def test_preview_blocked_for_executable_extension(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/setup.exe",
            name="setup",
            original_filename="setup.exe",
            file_size=10,
            mime_type="application/octet-stream",
            signature_status=Document.SignatureStatus.NOT_REQUIRED,
            is_confidential=False,
            tags=[],
            allowed_roles=[Document.AccessRole.HR],
        )
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(response.status_code, status.HTTP_501_NOT_IMPLEMENTED)

    # ── request-signature ─────────────────────────────────────────────

    def test_request_signature_creates_signers(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            f"/api/documents/{self.hr_doc.id}/request-signature/",
            {
                "signers": [
                    {"name": "Employee Test", "email": "employee@example.com"},
                    {"name": "Admin Test", "email": "admin@example.com"},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["signature_status"], Document.SignatureStatus.PENDING
        )
        self.assertEqual(len(response.data["signers"]), 2)
        signer = DocumentSigner.objects.get(
            document=self.hr_doc, email="employee@example.com"
        )
        self.assertIsNotNone(signer.requested_at)
        self.assertEqual(signer.requested_by, self.hr_profile)
        self.assertEqual(
            DocumentSignatureAuditLog.objects.filter(
                document=self.hr_doc,
                event=DocumentSignatureAuditLog.Event.REQUESTED,
            ).count(),
            2,
        )

    def test_request_signature_rejects_duplicate_emails(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            f"/api/documents/{self.hr_doc.id}/request-signature/",
            {
                "signers": [
                    {"name": "Jane", "email": "jane@co.com"},
                    {"name": "Jane Again", "email": "JANE@co.com"},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_request_signature_conflicts_when_already_pending(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            f"/api/documents/{self.confidential_doc.id}/request-signature/",
            {"signers": [{"name": "Employee", "email": "employee@example.com"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_request_signature_denied_for_employee(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{self.employee_doc.id}/request-signature/",
            {"signers": [{"name": "A", "email": "a@co.com"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_with_permission_can_request_signature(self):
        permission, _ = Permission.objects.get_or_create(
            module_name="Documents",
            feature_action="initiate_signature_requests",
        )
        self.employee_profile.add_permission(permission)
        self.employee_doc.signature_status = Document.SignatureStatus.NOT_REQUIRED
        self.employee_doc.save(update_fields=["signature_status"])

        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{self.employee_doc.id}/request-signature/",
            {"signers": [{"name": "Employee", "email": "employee@example.com"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_archived_document_cannot_be_requested_for_signature(self):
        self.hr_doc.archived = True
        self.hr_doc.save(update_fields=["archived"])
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            f"/api/documents/{self.hr_doc.id}/request-signature/",
            {"signers": [{"name": "A", "email": "a@co.com"}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authorized_signer_can_sign_and_persists_metadata(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/signable.pdf",
            name="Signable",
            original_filename="signable.pdf",
            allowed_roles=[Document.AccessRole.EMPLOYEE],
            signature_status=Document.SignatureStatus.NOT_REQUIRED,
        )
        signer = DocumentSigner.objects.create(
            document=doc,
            name="Employee User",
            email="employee@example.com",
            status=DocumentSigner.Status.PENDING,
        )
        doc.signature_status = Document.SignatureStatus.PENDING
        doc.save(update_fields=["signature_status"])

        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{doc.id}/sign/",
            {
                "signer_email": "employee@example.com",
                "signature": {
                    "type": "typed_name",
                    "value": "Employee User",
                    "accepted_terms": True,
                },
            },
            format="json",
            HTTP_USER_AGENT="UnitTest",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["document"]["signature_status"],
            Document.SignatureStatus.SIGNED,
        )
        signer.refresh_from_db()
        doc.refresh_from_db()
        self.assertEqual(signer.status, DocumentSigner.Status.SIGNED)
        self.assertIsNotNone(signer.signed_at)
        self.assertIsNotNone(doc.signed_at)
        self.assertEqual(signer.signed_by, self.employee_profile)
        self.assertTrue(signer.signature_hash)
        self.assertEqual(signer.signature_metadata["type"], "typed_name")
        self.assertEqual(signer.signature_metadata["user_agent"], "UnitTest")
        self.assertTrue(
            DocumentSignatureAuditLog.objects.filter(
                document=doc,
                signer=signer,
                event=DocumentSignatureAuditLog.Event.SIGNED,
            ).exists()
        )

    def test_unauthorized_user_cannot_sign_for_another_signer(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/signable.pdf",
            name="Signable",
            original_filename="signable.pdf",
            allowed_roles=[Document.AccessRole.EMPLOYEE],
            signature_status=Document.SignatureStatus.PENDING,
        )
        DocumentSigner.objects.create(
            document=doc,
            name="Other Person",
            email="other@example.com",
            status=DocumentSigner.Status.PENDING,
        )

        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{doc.id}/sign/",
            {
                "signer_email": "other@example.com",
                "signature": {
                    "type": "typed_name",
                    "value": "Other Person",
                    "accepted_terms": True,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_partial_multi_signer_flow_remains_pending_until_all_signed(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/multi.pdf",
            name="Multi",
            original_filename="multi.pdf",
            allowed_roles=[Document.AccessRole.EMPLOYEE],
            signature_status=Document.SignatureStatus.PENDING,
        )
        DocumentSigner.objects.create(
            document=doc,
            name="Employee User",
            email="employee@example.com",
            status=DocumentSigner.Status.PENDING,
        )
        DocumentSigner.objects.create(
            document=doc,
            name="Second User",
            email="second@example.com",
            status=DocumentSigner.Status.PENDING,
        )

        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{doc.id}/sign/",
            {
                "signer_email": "employee@example.com",
                "signature": {
                    "type": "typed_name",
                    "value": "Employee User",
                    "accepted_terms": True,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["document"]["signature_status"],
            Document.SignatureStatus.PENDING,
        )

        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            f"/api/documents/{doc.id}/sign/",
            {
                "signer_email": "second@example.com",
                "signature": {
                    "type": "typed_name",
                    "value": "Second User",
                    "accepted_terms": True,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["document"]["signature_status"],
            Document.SignatureStatus.SIGNED,
        )

    def test_archived_document_cannot_be_signed(self):
        doc = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/archived-sign.pdf",
            name="Archived Sign",
            original_filename="archived-sign.pdf",
            allowed_roles=[Document.AccessRole.EMPLOYEE],
            signature_status=Document.SignatureStatus.PENDING,
            archived=True,
        )
        DocumentSigner.objects.create(
            document=doc,
            name="Employee User",
            email="employee@example.com",
            status=DocumentSigner.Status.PENDING,
        )
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            f"/api/documents/{doc.id}/sign/",
            {
                "signer_email": "employee@example.com",
                "signature": {
                    "type": "typed_name",
                    "value": "Employee User",
                    "accepted_terms": True,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_signatures_endpoint_returns_audit_events(self):
        DocumentSigner.objects.create(
            document=self.hr_doc,
            name="Jane",
            email="jane@co.com",
            status=DocumentSigner.Status.PENDING,
        )
        DocumentSignatureAuditLog.objects.create(
            document=self.hr_doc,
            event=DocumentSignatureAuditLog.Event.REQUESTED,
            actor=self.hr_profile,
        )
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/documents/{self.hr_doc.id}/signatures/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["document_id"], self.hr_doc.id)
        self.assertEqual(len(response.data["signers"]), 1)
        self.assertEqual(len(response.data["audit_events"]), 1)

    def test_send_reminder_updates_pending_signers_and_audit(self):
        signer = DocumentSigner.objects.create(
            document=self.hr_doc,
            name="Jane",
            email="jane@co.com",
            status=DocumentSigner.Status.PENDING,
        )
        self.hr_doc.signature_status = Document.SignatureStatus.PENDING
        self.hr_doc.save(update_fields=["signature_status"])
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(f"/api/documents/{self.hr_doc.id}/send-reminder/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reminded_count"], 1)
        signer.refresh_from_db()
        self.assertIsNotNone(signer.last_reminded_at)
        self.assertTrue(
            DocumentSignatureAuditLog.objects.filter(
                document=self.hr_doc,
                signer=signer,
                event=DocumentSignatureAuditLog.Event.REMINDED,
            ).exists()
        )

    # ── versions ──────────────────────────────────────────────────────

    def test_versions_returns_count_and_results(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get(f"/api/documents/{self.employee_doc.id}/versions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)

    # ── bulk-delete ───────────────────────────────────────────────────

    def test_bulk_delete_by_admin_succeeds(self):
        self.client.force_authenticate(user=self.admin_user)
        d1 = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/bulk1.pdf",
            name="Bulk 1",
            original_filename="bulk1.pdf",
            allowed_roles=[Document.AccessRole.ADMIN],
        )
        d2 = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/bulk2.pdf",
            name="Bulk 2",
            original_filename="bulk2.pdf",
            allowed_roles=[Document.AccessRole.ADMIN],
        )
        response = self.client.post(
            "/api/documents/bulk-delete/",
            {"ids": [d1.id, d2.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk__in=[d1.id, d2.id]).exists())

    def test_bulk_delete_returns_404_for_missing_ids(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/documents/bulk-delete/",
            {"ids": [999998, 999999]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not_found", response.data)

    def test_bulk_delete_denied_for_employee(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.post(
            "/api/documents/bulk-delete/",
            {"ids": [self.employee_doc.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── bulk-archive ──────────────────────────────────────────────────

    def test_bulk_archive_by_hr_succeeds(self):
        self.client.force_authenticate(user=self.hr_user)
        d = Document.objects.create(
            uploaded_by=self.hr_profile,
            category=Document.Category.OTHER,
            file_key="documents/bulk-arc.pdf",
            name="Bulk Archive",
            original_filename="bulk-arc.pdf",
            allowed_roles=[Document.AccessRole.HR],
        )
        response = self.client.post(
            "/api/documents/bulk-archive/",
            {"ids": [d.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        d.refresh_from_db()
        self.assertTrue(d.archived)

    # ── export ────────────────────────────────────────────────────────

    def test_export_returns_url_for_hr(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/documents/export/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("export_url", response.data)

    def test_export_denied_for_employee(self):
        self.client.force_authenticate(user=self.employee_user)
        response = self.client.get("/api/documents/export/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── unauthenticated ───────────────────────────────────────────────

    def test_unauthenticated_request_returns_401(self):
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
