"""Prep Item domain model.

A prep item is a semi-finished product (e.g. hybrid meatballs) made on
prep-day. It has its own ingredients, a yield, and a storage zone. When
created it consumes source ingredients from the fridge; when a dish with
``prep_depends`` is cooked, the prep item is consumed.
"""

from dataclasses import dataclass, field


VALID_STORAGE_ZONES = ("fridge", "freezer", "pantry")


@dataclass
class PrepItem:
    """A semi-finished product stored for later use in dishes.

    Invariant: ``name`` is always stored stripped and lowercased, same as
    Dish.  Ingredient keys follow the same rule.
    """

    name: str
    ingredients: dict = field(default_factory=dict)  # name -> bool (essential/optional)
    yield_qty: int = 0
    yield_unit: str = "шт"
    storage: str = "freezer"
    remaining: int = 0  # how many units are left in storage

    def __post_init__(self):
        self.name = self._clean(self.name)
        if self.storage not in VALID_STORAGE_ZONES:
            raise ValueError(
                f"storage must be one of {VALID_STORAGE_ZONES}, got '{self.storage}'"
            )
        if self.ingredients:
            self.ingredients = {
                self._clean(k): v for k, v in self.ingredients.items()
            }

    @staticmethod
    def _clean(value, *, label="name"):
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a string, got {type(value).__name__}")
        return value.strip().lower()

    # ------------------------------------------------------------------
    # Normalization (delegates to the same strip().lower() rule as Dish)
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_name(name):
        return PrepItem._clean(name, label="prep item name")

    @staticmethod
    def normalize_ingredient(name):
        return PrepItem._clean(name, label="ingredient name")

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self):
        return {
            "name": self.name,
            "ingredients": self.ingredients,
            "yield": self.yield_qty,
            "yield_unit": self.yield_unit,
            "storage": self.storage,
            "remaining": self.remaining,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("prep item data must be a dict")

        name = cls.normalize_name(data["name"])
        if not name:
            raise ValueError("prep item name cannot be empty")

        raw_ingredients = data.get("ingredients", {})
        if not isinstance(raw_ingredients, dict):
            raise ValueError("ingredients must be a dict")

        item = cls(
            name=name,
            yield_qty=data.get("yield", 0),
            yield_unit=data.get("yield_unit", "шт"),
            storage=data.get("storage", "freezer"),
            remaining=data.get("remaining", data.get("yield", 0)),
        )

        for ing_name, is_essential in raw_ingredients.items():
            clean = cls.normalize_ingredient(ing_name)
            if not clean:
                continue
            if not isinstance(is_essential, bool):
                raise ValueError(
                    f"ingredient '{clean}': essential flag must be a boolean"
                )
            item.ingredients[clean] = is_essential

        return item
