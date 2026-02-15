from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.dispatch import receiver

class Unit(models.TextChoices):
    PCS = "pcs", "шт"
    METER = "m", "м"
    KG = "kg", "кг"


class Material(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    unit = models.CharField("Единица", max_length=10, choices=Unit.choices, default=Unit.PCS)
    stock = models.DecimalField("Остаток", max_digits=12, decimal_places=3, default=Decimal("0.000"))
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"

    def stock_display(self) -> Decimal:
        precision = Decimal("0.01") if self.unit in (Unit.METER, Unit.KG) else Decimal("0.1")
        return self.stock.quantize(precision, rounding=ROUND_HALF_UP)

    def clean(self):
        super().clean()
        if self.stock is not None:
            self.stock = self.stock_display()

    def __str__(self) -> str:
        return f"{self.name} ({self.stock_display()} {self.get_unit_display()})"


class Product(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    stock = models.DecimalField("Остаток", max_digits=12, decimal_places=3, default=Decimal("0.000"))
    piece_rate = models.DecimalField("Сдельная ставка (за 1 шт)", max_digits=12, decimal_places=2,
                                     default=Decimal("0.00"))
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Продукция"
        verbose_name_plural = "Продукция"

    def stock_display(self) -> Decimal:
        return self.stock.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    def clean(self):
        super().clean()
        if self.stock is not None:
            self.stock = self.stock_display()

    def __str__(self) -> str:
        return f"{self.name} (остаток {self.stock_display()} шт, ставка {self.piece_rate})"


class BillOfMaterial(models.Model):
    """
    Норма расхода: сколько материала нужно на 1 единицу продукции.
    Пример: "Бумага с ёлочками" -> 0.500 м на 1 "Красный подарок"
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name="Продукция", related_name="bom_items")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, verbose_name="Материал", related_name="bom_items")
    qty_per_one = models.DecimalField("На 1 шт (кол-во)", max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Норма списания"
        verbose_name_plural = "Нормы списания"
        constraints = [
            models.UniqueConstraint(fields=["product", "material"], name="uq_bom_product_material")
        ]

    def __str__(self) -> str:
        return f"{self.product.name} → {self.material.name}: {self.qty_per_one} {self.material.get_unit_display()}/шт"


class StockMovementType(models.TextChoices):
    IN_ = "in", "Приход"
    OUT = "out", "Расход"

class BatchStatus(models.TextChoices):
    PLANNED = "planned", "Запланирована"
    IN_PROGRESS = "in_progress", "В работе"
    DONE = "done", "Готово"
    CANCELED = "canceled", "Отменена"


class Batch(models.Model):
    created_at = models.DateTimeField("Создана", default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_batches",
        verbose_name="Кто создал",
    )

    name = models.CharField("Название/номер партии", max_length=120)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="Продукция")
    planned_qty = models.DecimalField("План (шт)", max_digits=12, decimal_places=3, default=Decimal("0.000"))

    status = models.CharField("Статус", max_length=20, choices=BatchStatus.choices, default=BatchStatus.PLANNED)
    due_date = models.DateField("Дедлайн", null=True, blank=True)

    note = models.CharField("Комментарий", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Партия"
        verbose_name_plural = "Партии"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} — {self.product.name} (план {self.planned_qty})"



class StockMovement(models.Model):
    """
    Единый журнал движений:
    - material или product (одно из двух)
    - тип (приход/расход)
    - количество
    - ссылка на операцию производства (пока только она)
    """
    created_at = models.DateTimeField("Дата/время", default=timezone.now, db_index=True)
    movement_type = models.CharField("Тип", max_length=10, choices=StockMovementType.choices)
    qty = models.DecimalField("Количество", max_digits=12, decimal_places=3)

    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Материал")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Продукция")

    production_op = models.ForeignKey(
        "ProductionOperation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="movements",
        verbose_name="Операция производства",
    )

    comment = models.CharField("Комментарий", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Движение склада"
        verbose_name_plural = "Движения склада"
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def clean(self):
        super().clean()
        if bool(self.material) == bool(self.product):
            raise ValidationError("Нужно указать либо материал, либо продукцию (только одно).")

    def __str__(self) -> str:
        obj = self.material or self.product
        return f"{self.get_movement_type_display()}: {obj} x {self.qty} ({self.created_at:%Y-%m-%d %H:%M})"


class ProductionOperation(models.Model):
    """
    Сотрудник сделал N единиц продукции.
    При создании:
      - списываем материалы по BOM
      - увеличиваем склад продукции
      - пишем движения склада
    """
    created_at = models.DateTimeField("Дата/время", default=timezone.now, db_index=True)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Сотрудник")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="Продукция")
    qty = models.DecimalField("Количество (шт)", max_digits=12, decimal_places=3)
    batch = models.ForeignKey(
        Batch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Партия",
        related_name="operations",
    )

    pay_rate = models.DecimalField(
        "Ставка (за 1 шт)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    pay_total = models.DecimalField(
        "Начислено",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    note = models.CharField("Комментарий", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Операция производства"
        verbose_name_plural = "Операции производства"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} — {self.employee} сделал {self.qty} шт {self.product.name}"

    def clean(self):
        super().clean()
        if self.qty is None or self.qty <= 0:
            raise ValidationError({"qty": "Количество должно быть больше 0."})
        if self.qty % 1 != 0:
            raise ValidationError({"qty": "Количество продукции должно быть целым числом."})

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None

        # стандартные проверки модели
        self.full_clean()

        super().save(*args, **kwargs)

        # чтобы не задвоить списание при редактировании:
        if not is_new:
            return

        # Лочим запись продукта (и материалы) на время списания
        product = Product.objects.select_for_update().get(pk=self.product_id)

        # Проверяем BOM: если норм нет — считаем ошибкой MVP (чтобы не было "сделал", а списать нечего)
        bom = list(
            BillOfMaterial.objects.select_related("material")
            .select_for_update(of=("self",))  # на всякий случай
            .filter(product_id=product.pk)
        )
        if not bom:
            raise ValidationError("Для этой продукции не заданы нормы списания (BOM).")

        # 1) проверка остатков материалов
        shortages = []
        required_map: list[tuple[Material, Decimal]] = []
        for item in bom:
            mat = Material.objects.select_for_update().get(pk=item.material_id)
            required = (item.qty_per_one * self.qty).quantize(Decimal("0.001"))
            required_map.append((mat, required))
            if mat.stock < required:
                shortages.append(f"{mat.name}: нужно {required} {mat.get_unit_display()}, есть {mat.stock}")

        if shortages:
            # откат транзакции + человекопонятная ошибка
            raise ValidationError("Недостаточно материалов:\n- " + "\n- ".join(shortages))

        # 2) списываем материалы + движения
        for mat, required in required_map:
            mat.stock = (mat.stock - required).quantize(Decimal("0.001"))
            mat.save(update_fields=["stock"])

            StockMovement.objects.create(
                movement_type=StockMovementType.OUT,
                qty=required,
                material=mat,
                production_op=self,
                comment=f"Списание по норме на {self.qty} шт {product.name}",
            )

        # 3) приход продукции + движение
        product.stock = (product.stock + self.qty).quantize(Decimal("0.001"))
        product.save(update_fields=["stock"])

        StockMovement.objects.create(
            movement_type=StockMovementType.IN_,
            qty=self.qty,
            product=product,
            production_op=self,
            comment="Поступление готовой продукции по операции производства",
        )
        # 4) расчёт оплаты
        self.pay_rate = product.piece_rate
        self.pay_total = (self.pay_rate * self.qty).quantize(Decimal("0.01"))
        super().save(update_fields=["pay_rate", "pay_total"])

class AdjustmentTarget(models.TextChoices):
    MATERIAL = "material", "Материал"
    PRODUCT = "product", "Продукция"


class StockAdjustment(models.Model):
    created_at = models.DateTimeField("Дата/время", default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name="Кто внёс",
        related_name="stock_adjustments",
    )

    target_type = models.CharField("Тип", max_length=20, choices=AdjustmentTarget.choices)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Материал")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Продукция")

    movement_type = models.CharField("Операция", max_length=10, choices=StockMovementType.choices)
    qty = models.DecimalField("Количество", max_digits=12, decimal_places=3)

    reason = models.CharField("Причина/комментарий", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Корректировка склада"
        verbose_name_plural = "Корректировки склада"
        ordering = ["-created_at"]

    def clean(self):
        super().clean()

        if self.qty is None or self.qty <= 0:
            raise ValidationError({"qty": "Количество должно быть больше 0."})

        # Выбор цели должен соответствовать типу
        if self.target_type == AdjustmentTarget.MATERIAL:
            if not self.material or self.product:
                raise ValidationError("Для типа 'Материал' нужно указать только материал.")
        elif self.target_type == AdjustmentTarget.PRODUCT:
            if not self.product or self.material:
                raise ValidationError("Для типа 'Продукция' нужно указать только продукцию.")
        else:
            raise ValidationError("Некорректный тип корректировки.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        self.full_clean()
        super().save(*args, **kwargs)

        if not is_new:
            # На MVP не пересчитываем при редактировании
            return

        comment = f"Корректировка: {self.reason}".strip()

        if self.target_type == AdjustmentTarget.MATERIAL:
            mat = Material.objects.select_for_update().get(pk=self.material_id)
            if self.movement_type == StockMovementType.OUT and mat.stock < self.qty:
                raise ValidationError(
                    f"Недостаточно материала '{mat.name}': "
                    f"нужно {self.qty} {mat.get_unit_display()}, есть {mat.stock}"
                )

            mat.stock = (mat.stock + self.qty) if self.movement_type == StockMovementType.IN_ else (mat.stock - self.qty)
            mat.save(update_fields=["stock"])

            StockMovement.objects.create(
                movement_type=self.movement_type,
                qty=self.qty,
                material=mat,
                comment=comment or "Корректировка материала",
            )

        if self.target_type == AdjustmentTarget.PRODUCT:
            prod = Product.objects.select_for_update().get(pk=self.product_id)
            if self.movement_type == StockMovementType.OUT and prod.stock < self.qty:
                raise ValidationError(
                    f"Недостаточно продукции '{prod.name}': нужно {self.qty}, есть {prod.stock}"
                )

            prod.stock = (prod.stock + self.qty) if self.movement_type == StockMovementType.IN_ else (prod.stock - self.qty)
            prod.save(update_fields=["stock"])

            StockMovement.objects.create(
                movement_type=self.movement_type,
                qty=self.qty,
                product=prod,
                comment=comment or "Корректировка продукции",
            )


class Shift(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="Сотрудник")
    started_at = models.DateTimeField("Начало смены", default=timezone.now, db_index=True)
    ended_at = models.DateTimeField("Конец смены", null=True, blank=True, db_index=True)

    hours = models.DecimalField("Часы", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    note = models.CharField("Комментарий", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        ordering = ["-started_at"]

    def clean(self):
        super().clean()
        if self.ended_at and self.ended_at <= self.started_at:
            raise ValidationError("Конец смены должен быть позже начала.")

    def _calc_hours(self) -> Decimal:
        if not self.ended_at:
            return Decimal("0.00")
        seconds = (self.ended_at - self.started_at).total_seconds()
        h = Decimal(str(seconds)) / Decimal("3600")
        return h.quantize(Decimal("0.01"))

    @transaction.atomic
    def close(self):
        if self.ended_at:
            return
        self.ended_at = timezone.now()
        self.hours = self._calc_hours()
        self.full_clean()
        self.save(update_fields=["ended_at", "hours"])