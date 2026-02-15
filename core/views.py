from __future__ import annotations
from django.db.models import Sum
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Product, ProductionOperation, Shift
from django.http import HttpResponseForbidden
from .models import Batch, BatchStatus
from django.contrib.admin.views.decorators import staff_member_required


def home(request):
    # Удобно: если залогинен — сразу в рабочий экран
    if request.user.is_authenticated:
        return redirect("core:work")
    return redirect("/admin/login/?next=/work/")


@login_required
def work(request):
    today = timezone.localdate()

    # операции за сегодня
    ops_qs = (
        ProductionOperation.objects
        .select_related("product", "employee")
        .filter(employee=request.user, created_at__date=today)
        .order_by("-created_at")
    )
    ops = ops_qs[:100]
    piece_today = ops_qs.aggregate(s=Sum("pay_total"))["s"] or Decimal("0.00")

    # смены за сегодня (закрытые)
    shifts_qs = Shift.objects.filter(employee=request.user, started_at__date=today).order_by("-started_at")
    hours_today = shifts_qs.aggregate(s=Sum("hours"))["s"] or Decimal("0.00")

    # активная смена
    open_shift = Shift.objects.filter(employee=request.user, ended_at__isnull=True).first()

    # KPI: выработка/час (по сдельным штукам в час)
    total_qty = ops_qs.aggregate(s=Sum("qty"))["s"] or Decimal("0.00")
    productivity = (total_qty / hours_today).quantize(Decimal("0.01")) if hours_today and hours_today > 0 else Decimal("0.00")

    total_today = piece_today.quantize(Decimal("0.01"))

    products = Product.objects.filter(is_active=True).order_by("name")

    batches_qs = Batch.objects.filter(
        status__in=[BatchStatus.PLANNED, BatchStatus.IN_PROGRESS]
    ).select_related("product").order_by("-created_at")

    batches = []
    for b in batches_qs:
        done = b.operations.aggregate(x=Sum("qty"))["x"] or Decimal("0.000")
        remaining = (b.planned_qty - done).quantize(Decimal("0.001"))
        if remaining < 0:
            remaining = Decimal("0.000")
        batches.append({"obj": b, "remaining": remaining, "done": done})

    return render(request, "core/work.html", {
        "ops": ops,
        "batches": batches,
        "piece_today": piece_today,
        "hours_today": hours_today,
        "productivity": productivity,
        "total_today": total_today,
        "open_shift": open_shift,
    })


@login_required
def production_create(request):
    if request.method != "POST":
        return redirect("core:work")

    batch_id = request.POST.get("batch")

    qty_raw = request.POST.get("qty", "").replace(",", ".").strip()

    if not batch_id:
        messages.error(request, "Выберите партию.")

    try:
        qty = float(qty_raw)
    except ValueError:
        messages.error(request, "Введите корректное количество.")
        return redirect("core:work")

    try:
        batch = Batch.objects.select_related("product").get(pk=batch_id)
    except Batch.DoesNotExist:
        messages.error(request, "Партия не найдена.")
        return redirect("core:work")

    if batch.status == BatchStatus.PLANNED:
        batch.status = BatchStatus.IN_PROGRESS
        batch.save(update_fields=["status"])

    try:
        op = ProductionOperation(
            employee=request.user,
            product=batch.product,
            batch=batch,
            qty=qty,
        )
        op.save()
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("core:work")

    messages.success(request, f"Готово! {op.qty} шт — {batch.product.name} (партия {batch.name})")

    return redirect("core:work")

@login_required
def shift_start(request):
    if request.method != "POST":
        return redirect("core:work")

    # нельзя начать вторую смену, если есть открытая
    open_shift = Shift.objects.filter(employee=request.user, ended_at__isnull=True).first()
    if open_shift:
        messages.error(request, "Смена уже начата.")
        return redirect("core:work")

    Shift.objects.create(employee=request.user)
    messages.success(request, "Смена начата.")
    return redirect("core:work")


@login_required
def shift_stop(request):
    if request.method != "POST":
        return redirect("core:work")

    open_shift = Shift.objects.filter(employee=request.user, ended_at__isnull=True).first()
    if not open_shift:
        messages.error(request, "Нет активной смены.")
        return redirect("core:work")

    open_shift.close()
    messages.success(request, f"Смена закрыта. Часы: {open_shift.hours}")
    return redirect("core:work")

@staff_member_required
def admin_report(request):
    # период: по умолчанию последние 7 дней
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    # если не передали — ставим дефолт
    today = timezone.localdate()
    if not date_to:
        date_to = str(today)
    if not date_from:
        date_from = str(today - timezone.timedelta(days=7))

    # операции и смены за период
    ops = ProductionOperation.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    shifts = Shift.objects.filter(started_at__date__gte=date_from, started_at__date__lte=date_to)

    # агрегация по сотруднику
    ops_by = ops.values("employee__username").annotate(piece=Sum("pay_total"), qty=Sum("qty")).order_by("employee__username")
    sh_by = shifts.values("employee__username").annotate(hours=Sum("hours")).order_by("employee__username")

    # склеим в словарь
    result = {}
    for row in ops_by:
        u = row["employee__username"]
        result.setdefault(u, {"qty": Decimal("0.00"), "piece": Decimal("0.00"), "hours": Decimal("0.00")})
        result[u]["qty"] = row["qty"] or Decimal("0.00")
        result[u]["piece"] = row["piece"] or Decimal("0.00")

    for row in sh_by:
        u = row["employee__username"]
        result.setdefault(u, {"qty": Decimal("0.00"), "piece": Decimal("0.00"), "hours": Decimal("0.00")})
        result[u]["hours"] = row["hours"] or Decimal("0.00")


    # посчитаем итого и выработку/час
    rows = []
    for u, v in sorted(result.items()):
        total = v["piece"].quantize(Decimal("0.01"))
        prod = (v["qty"] / v["hours"]).quantize(Decimal("0.01")) if v["hours"] and v["hours"] > 0 else Decimal("0.00")
        rows.append({"user": u, **v, "total": total, "productivity": prod})

    return render(request, "core/admin_report.html", {"rows": rows, "date_from": date_from, "date_to": date_to})

@staff_member_required
def batches_list(request):
    qs = Batch.objects.select_related("product").all().order_by("-created_at")[:200]
    data = []
    for b in qs:
        done = b.operations.aggregate(x=Sum("qty"))["x"] or Decimal("0.000")
        remaining = (b.planned_qty - done).quantize(Decimal("0.001"))
        if remaining < 0:
            remaining = Decimal("0.000")
        data.append({"b": b, "done": done, "remaining": remaining})
    return render(request, "core/batches_list.html", {"data": data})
