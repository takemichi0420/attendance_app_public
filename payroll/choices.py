from django.db import models

# payroll/choices.py

class WageType:
    HOURLY = "hourly"
    SALARY = "salary"
    CHOICES = [
        (HOURLY, "時給"),
        (SALARY, "月給"),
    ]
