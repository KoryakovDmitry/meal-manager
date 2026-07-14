from dataclasses import dataclass, field


MAX_INSTRUCTIONS_LENGTH = 20_000


@dataclass
class Dish:
    """Recipe model.

    Invariant: ``name`` is always stored stripped and lowercased. The
    ``__post_init__`` enforces this on every construction path (direct,
    ``from_dict``, dataclass replace), so consumers can compare ``dish.name``
    by equality without re-normalizing.
    """

    name: str
    ingredients: dict = field(default_factory=dict)
    prep_depends: list = field(default_factory=list)  # prep item names this dish needs
    instructions: str | None = None

    def __post_init__(self):
        self.name = self.normalize_name(self.name)
        if self.instructions is not None:
            if not isinstance(self.instructions, str):
                raise ValueError("instructions must be a string or null")
            instructions = self.instructions.strip()
            if len(instructions) > MAX_INSTRUCTIONS_LENGTH:
                raise ValueError(
                    f"instructions cannot exceed {MAX_INSTRUCTIONS_LENGTH} characters"
                )
            self.instructions = instructions or None
        # Enforce the same normalization invariant on ingredient keys for every
        # construction path (direct, dataclasses.replace, …), so consumers can
        # compare against the always-lowercased fridge without re-normalizing.
        if self.ingredients:
            self.ingredients = {
                self.normalize_ingredient(key): value
                for key, value in self.ingredients.items()
            }

    @staticmethod
    def _clean(value, *, label):
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a string, got {type(value).__name__}")
        return value.strip().lower()

    @staticmethod
    def normalize_ingredient(name):
        return Dish._clean(name, label="ingredient name")

    @staticmethod
    def normalize_name(name):
        return Dish._clean(name, label="dish name")

    def add_ingredient(self, ingredient_name, is_essential=True):
        if not isinstance(is_essential, bool):
            raise ValueError("ingredient essential flag must be a boolean")
        ingredient = self.normalize_ingredient(ingredient_name)
        if not ingredient:
            raise ValueError("ingredient name cannot be empty")
        self.ingredients[ingredient] = is_essential

    def can_cook_with(self, available_ingredients):
        for ingredient, essential in self.ingredients.items():
            if essential and ingredient not in available_ingredients:
                return False
        return True

    def to_dict(self):
        result = {
            "name": self.name,
            "ingredients": self.ingredients,
        }
        if self.prep_depends:
            result["prep_depends"] = list(self.prep_depends)
        if self.instructions is not None:
            result["instructions"] = self.instructions
        return result

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("dish data must be a dict")

        name = cls.normalize_name(data["name"])
        if not name:
            raise ValueError("dish name cannot be empty")

        raw_ingredients = data.get("ingredients", {})
        if not isinstance(raw_ingredients, dict):
            raise ValueError("ingredients must be a dict")

        raw_prep_depends = data.get("prep_depends", [])
        if not isinstance(raw_prep_depends, list):
            raw_prep_depends = []

        dish = cls(
            name=name,
            prep_depends=[
                cls.normalize_name(pd)
                for pd in raw_prep_depends
                if isinstance(pd, str)
            ],
            instructions=data.get("instructions"),
        )
        for ingredient_name, is_essential in raw_ingredients.items():
            dish.add_ingredient(ingredient_name, is_essential)
        return dish
