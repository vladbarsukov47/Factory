from django.contrib import admin
from . import models
from .models import StockAdjustment, Product, Material, ProductionOperation, BillOfMaterial
from .models import Shift, Batch
from django.db.models import Sum
from decimal import Decimal

@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "stock_rounded", "is_active")
    list_filter = ("unit", "is_active")
    search_fields = ("name",)   # ← ВАЖНО
    ordering = ("name",)

    def stock_rounded(self, obj):
        return obj.stock_display()

    stock_rounded.short_description = "Остаток"
    stock_rounded.admin_order_field = "stock"

@admin.register(ProductionOperation)
class ProductionOperationAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "employee", "product", "batch", "qty",
        "pay_rate", "pay_total", "note"
    )
    list_filter = ("product", "batch", "employee")
    search_fields = ("product__name", "batch__name", "employee__username")
    autocomplete_fields = ("product", "batch", "employee")
    readonly_fields = ("created_at", "pay_rate", "pay_total")
    ordering = ("-created_at",)

class BillOfMaterialInline(admin.TabularInline):
    model = BillOfMaterial
    extra = 1
    autocomplete_fields = ("material",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "stock_rounded", "piece_rate", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)
    inlines = (BillOfMaterialInline,)

    def stock_rounded(self, obj):
        return obj.stock_display()

    stock_rounded.short_description = "Остаток"
    stock_rounded.admin_order_field = "stock"


admin.site.register(models.StockMovement)

@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "created_by", "target_type", "movement_type", "material", "product", "qty", "reason")
    list_filter = ("target_type", "movement_type")
    search_fields = ("reason", "material__name", "product__name", "created_by__username")
    autocomplete_fields = ("created_by", "material", "product")
    readonly_fields = ("created_at",)

    class Media:
        js = ("core/stock_adjustment.js",)

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("started_at", "ended_at", "employee", "hours", "note")
    list_filter = ("employee",)
    search_fields = ("employee__username", "note")
    autocomplete_fields = ("employee",)
    readonly_fields = ("hours",)


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "product", "planned_qty", "status", "done_qty", "remaining_qty", "due_date")
    list_filter = ("status", "product")
    search_fields = ("name", "product__name")
    autocomplete_fields = ("created_by", "product")
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def done_qty(self, obj):
        s = obj.operations.aggregate(x=Sum("qty"))["x"] or Decimal("0.000")
        return s
    done_qty.short_description = "Сделано"

    def remaining_qty(self, obj):
        done = obj.operations.aggregate(x=Sum("qty"))["x"] or Decimal("0.000")
        rem = (obj.planned_qty - done).quantize(Decimal("0.001"))
        return rem if rem > 0 else Decimal("0.000")
    remaining_qty.short_description = "Осталось"