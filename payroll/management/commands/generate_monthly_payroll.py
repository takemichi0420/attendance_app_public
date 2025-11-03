from django.core.management.base import BaseCommand, CommandParser
from payroll.services import generate_monthly_payroll

class Command(BaseCommand):
    help = "Rebuild monthly payroll for the given year & month."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)

    def handle(self, *args, **opts):
        y = int(opts["year"])
        m = int(opts["month"])
        ym = f"{y:04d}{m:02d}"
        results = generate_monthly_payroll(ym)  # ← 1引数に統一
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {len(results)} records for {ym}"))