from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("work/", views.work, name="work"),
    path("work/new/", views.production_create, name="production_create"),

    path("work/shift/start/", views.shift_start, name="shift_start"),
    path("work/shift/stop/", views.shift_stop, name="shift_stop"),

    path("report/", views.admin_report, name="admin_report"),  # отчёт админа
    path("batches/", views.batches_list, name="batches_list"),

]
