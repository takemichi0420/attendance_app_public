from django.db import migrations

def forward_sync_names(apps, schema_editor):
    Staff       = apps.get_model('payroll', 'Staff')
    StaffProfile = apps.get_model('payroll', 'StaffProfile')

    # まとめて更新したいので一旦 dict にする
    staff_name_dict = dict(
        Staff.objects.values_list('id', 'name')
    )

    # iterator() で少しずつメモリに載せる

def rollback_noop(apps, schema_editor):
    """逆マイグレーション時は何もしない（今回は戻さなくて良い想定）"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        # 直前のスキーママイグレーションを必ず入れる
        ('payroll', '0024_add_employment_insured_and_cleanup'),
    ]

    operations = [
    ]