# attendance_app/signals.py

import qrcode
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.dispatch import receiver

from payroll.models import Staff
from payroll.models import StaffProfile

User = get_user_model()


def create_blank_user_for_staff(staff):
    """
    Staff 登録時に連動して作るログイン不可ユーザー。
    username = 'staff<pk>' で衝突があれば連番を付与。
    """
    base = f"staff{staff.pk}"
    username = base
    i = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}_{i}"
        i += 1

    user = User.objects.create(username=username, first_name=staff.name)
    user.set_unusable_password()
    user.save()
    return user


@receiver(post_save, sender=Staff)
def create_profile_and_qr(sender, instance, created, **kwargs):
    """
    Staff が作られたときだけ走るシグナル。
    - User を作成
    - StaffProfile を作成
    - QR コードを生成＆保存
    """
    if not created:
        return

    # 1) ログイン不可ユーザーを自動作成
    user = User.objects.get(username=instance.name)
    # user = create_blank_user_for_staff(instance)

    user.set_unusable_password()
    user.save()

    # 2) StaffProfile を必ず作成（重複防止に get_or_create）
    profile, _ = StaffProfile.objects.get_or_create(user=user, staff=instance)

    # 3) QR コード生成
    qr = qrcode.make(str(profile.qr_token))
    buf = BytesIO()
    buf.seek(0)

    qr.save(buf, format="PNG")
    profile.qr_image.save(
        f"staff_qr_{instance.pk}.png",
        ContentFile(buf.getvalue()),
        save=True
    )


@receiver(post_save, sender=Staff)
def ensure_profile(sender, instance, created, **kwargs):
    """
    CSV インポートや管理画面からの追加など、
    フォーム以外のルートでも必ず Profile があるようにする
    """
    if created:
        # username が 'staff<pk>' で作られていることが前提
        try:
            user = User.objects.get(username=f"staff{instance.pk}")
        except User.DoesNotExist:
            return
        StaffProfile.objects.get_or_create(user=user, staff=instance)



