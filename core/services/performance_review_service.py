from datetime import datetime, time, timedelta

from django.utils import timezone

from core.models import (
    PerformanceReview,
    PerformanceReviewHistoryEvent,
    PerformanceReviewReminder,
    UserProfile,
)


def _normalized_offsets(reminder_offsets_days) -> list[int]:
    if not isinstance(reminder_offsets_days, list):
        return []

    offsets: set[int] = set()
    for offset in reminder_offsets_days:
        try:
            normalized_offset = int(offset)
        except (TypeError, ValueError):
            continue
        if normalized_offset < 0:
            continue
        offsets.add(normalized_offset)
    return sorted(offsets)


def _as_scheduled_datetime(target_date) -> datetime:
    naive = datetime.combine(target_date, time(hour=9, minute=0))
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _review_recipients(review: PerformanceReview) -> list[UserProfile]:
    recipients: list[UserProfile] = [review.employee]
    if review.reviewer and review.reviewer_id != review.employee_id:
        recipients.append(review.reviewer)
    return recipients


def _review_employee_name(review: PerformanceReview) -> str:
    return review.employee.user.get_full_name() or review.employee.user.username


def _build_upcoming_message(review: PerformanceReview, offset_days: int) -> str:
    employee_name = _review_employee_name(review)
    review_type = review.get_review_type_display()
    return (
        f"{review_type} for {employee_name} is due in {offset_days} day(s) "
        f"on {review.scheduled_date.isoformat()}."
    )


def _build_due_today_message(review: PerformanceReview) -> str:
    employee_name = _review_employee_name(review)
    review_type = review.get_review_type_display()
    return f"{review_type} for {employee_name} is due today."


def _build_overdue_message(review: PerformanceReview) -> str:
    employee_name = _review_employee_name(review)
    review_type = review.get_review_type_display()
    return (
        f"{review_type} for {employee_name} is overdue since "
        f"{review.scheduled_date.isoformat()}."
    )


def _ensure_reminder(
    review: PerformanceReview,
    recipient: UserProfile,
    reminder_type: str,
    message: str,
    scheduled_for: datetime,
    actor: UserProfile | None,
) -> tuple[PerformanceReviewReminder, bool]:
    reminder, created = PerformanceReviewReminder.objects.get_or_create(
        review=review,
        recipient=recipient,
        reminder_type=reminder_type,
        scheduled_for=scheduled_for,
        defaults={"message": message},
    )

    if not created and reminder.message != message:
        reminder.message = message
        reminder.save(update_fields=["message"])

    if created:
        PerformanceReviewHistoryEvent.objects.create(
            review=review,
            actor=actor,
            event_type=PerformanceReviewHistoryEvent.EventType.REMINDER_GENERATED,
            description=f"{reminder.get_reminder_type_display()} reminder generated.",
            metadata={
                "reminder_id": reminder.id,
                "recipient_id": recipient.id,
                "reminder_type": reminder.reminder_type,
                "scheduled_for": reminder.scheduled_for.isoformat(),
            },
        )

    return reminder, created


def _mark_due_reminders_as_sent(
    review: PerformanceReview, reference_time: datetime
) -> int:
    due_ids = list(
        PerformanceReviewReminder.objects.filter(
            review=review,
            is_sent=False,
            scheduled_for__lte=reference_time,
        ).values_list("id", flat=True)
    )
    if not due_ids:
        return 0

    PerformanceReviewReminder.objects.filter(id__in=due_ids).update(
        is_sent=True,
        sent_at=reference_time,
    )
    return len(due_ids)


def sync_performance_review_reminders_for_review(
    review: PerformanceReview,
    *,
    actor: UserProfile | None = None,
    reference_time: datetime | None = None,
) -> dict[str, int]:
    if reference_time is None:
        reference_time = timezone.now()

    created_count = 0
    today = timezone.localdate(reference_time)

    if review.status in [
        PerformanceReview.Status.COMPLETED,
        PerformanceReview.Status.CANCELLED,
    ]:
        sent_count = _mark_due_reminders_as_sent(review, reference_time)
        return {"created_count": created_count, "sent_count": sent_count}

    offsets = _normalized_offsets(review.reminder_offsets_days)
    recipients = _review_recipients(review)

    for recipient in recipients:
        for offset in offsets:
            reminder_date = review.scheduled_date - timedelta(days=offset)
            reminder_dt = _as_scheduled_datetime(reminder_date)
            _, created = _ensure_reminder(
                review=review,
                recipient=recipient,
                reminder_type=PerformanceReviewReminder.ReminderType.UPCOMING,
                message=_build_upcoming_message(review, offset),
                scheduled_for=reminder_dt,
                actor=actor,
            )
            if created:
                created_count += 1

        if review.scheduled_date <= today:
            due_today_dt = _as_scheduled_datetime(review.scheduled_date)
            _, created = _ensure_reminder(
                review=review,
                recipient=recipient,
                reminder_type=PerformanceReviewReminder.ReminderType.DUE_TODAY,
                message=_build_due_today_message(review),
                scheduled_for=due_today_dt,
                actor=actor,
            )
            if created:
                created_count += 1

        if review.scheduled_date < today:
            overdue_dt = _as_scheduled_datetime(
                review.scheduled_date + timedelta(days=1)
            )
            _, created = _ensure_reminder(
                review=review,
                recipient=recipient,
                reminder_type=PerformanceReviewReminder.ReminderType.OVERDUE,
                message=_build_overdue_message(review),
                scheduled_for=overdue_dt,
                actor=actor,
            )
            if created:
                created_count += 1

    sent_count = _mark_due_reminders_as_sent(review, reference_time)
    return {"created_count": created_count, "sent_count": sent_count}


def materialize_performance_review_reminders(
    *,
    actor: UserProfile | None = None,
    reference_time: datetime | None = None,
) -> dict[str, int]:
    if reference_time is None:
        reference_time = timezone.now()

    totals = {
        "reviews_processed": 0,
        "created_count": 0,
        "sent_count": 0,
    }
    reviews = PerformanceReview.objects.select_related(
        "employee__user",
        "reviewer__user",
    ).all()

    for review in reviews:
        counts = sync_performance_review_reminders_for_review(
            review,
            actor=actor,
            reference_time=reference_time,
        )
        totals["reviews_processed"] += 1
        totals["created_count"] += counts["created_count"]
        totals["sent_count"] += counts["sent_count"]

    return totals
