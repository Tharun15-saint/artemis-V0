from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func
from database.base import Base


class FabricKnitting(Base):
    """
    World 1 / Layer 2: Raw greige fabric output of the knitting stage.

    yarn_to_fabric_ratio is the most important learned coefficient in the product chain:
    it determines how many kg of yarn are consumed per kg of greige fabric produced.
    For single jersey 140gsm, the industry prior is ~1.08; for 3-thread fleece it is ~1.42.
    This ratio, when calibrated against RRK's actual GRN vs. fabric yield records, becomes
    the foundation of the fabric cost calculation.

    Relationship: yarn_id → yarn.yarn_id (the specific lot of yarn consumed)
    """
    __tablename__ = "fabric_knitting"

    knitting_id                 = Column(Integer, primary_key=True)
    yarn_id                     = Column(Integer, nullable=True)    # FK → yarn.yarn_id
    knit_structure              = Column(String(50), nullable=False)
    # single_jersey | double_jersey | interlock | rib_1x1 | rib_2x2
    # french_terry | fleece_3thread | fleece_2thread | pique | waffle | jacquard
    weight_gsm_greige           = Column(Numeric(8, 2))
    weight_gsm_finished_target  = Column(Numeric(8, 2))
    machine_gauge               = Column(Integer)           # 24G | 28G | 32G
    knitting_unit_id            = Column(Integer, nullable=True)    # FK → knitting_mills
    knitting_in_house           = Column(Boolean, default=False)
    quantity_kg                 = Column(Numeric(12, 4))
    cost_per_kg_inr             = Column(Numeric(10, 4))
    yarn_to_fabric_ratio        = Column(Numeric(8, 4))     # kg yarn / kg greige fabric
    yield_rate_pct              = Column(Numeric(5, 2))     # % of yarn → usable greige
    shrinkage_potential_pct     = Column(Numeric(5, 2))     # expected post-wash shrinkage
    complexity_rating           = Column(Integer)           # 1-5
    batch_reference             = Column(String(100))
    production_date             = Column(Date)
    quality_result              = Column(String(50))        # passed | rejected | rework
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class FabricDyeing(Base):
    """
    World 1 / Layer 3: Fabric dyeing stage.

    bypassed=True when the upstream yarn was pre-dyed (colour_state = yarn_dyed or solution_dyed
    on the Yarn record). In that case the knitting_id still links back to the greige batch but
    no dye cost is incurred. The field must always be recorded — a silent NULL would be ambiguous.

    colour_complexity_class drives the dyeing cost premium that gets fed into LearnedCoefficient.
    """
    __tablename__ = "fabric_dyeing"

    dyeing_id                   = Column(Integer, primary_key=True)
    knitting_id                 = Column(Integer, nullable=False)   # FK → fabric_knitting
    bypassed                    = Column(Boolean, default=False, nullable=False)
    dye_method                  = Column(String(50))
    # reactive | pigment | vat | discharge | space_dye | tie_dye | overdye
    colour_category             = Column(String(50))
    # white | black | dark | medium | light | heather | melange | stripe
    colour_complexity_class     = Column(String(50))
    # standard | dark_premium | special_effect | double_colour | melange_special
    lab_dip_rounds              = Column(Integer, default=1)
    dyeing_unit_id              = Column(Integer, nullable=True)    # FK → dyeing_units
    dyeing_in_house             = Column(Boolean, default=False)
    quantity_kg                 = Column(Numeric(12, 4))
    cost_per_kg_inr             = Column(Numeric(10, 4))
    chemical_cost_per_kg_inr    = Column(Numeric(10, 4))
    water_litres_per_kg         = Column(Numeric(8, 2))
    shade_pass_rate_pct         = Column(Numeric(5, 2))
    production_date             = Column(Date)
    quality_result              = Column(String(50))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class FabricFinishing(Base):
    """
    World 1 / Layer 4: Final finishing stage (compacting, stentering, brushing, enzyme wash).

    total_fabric_cost_per_kg_inr is the materialized output of the full cost chain:
        total = knitting_cost + dyeing_cost + finishing_cost + chemical_cost

    This field is the direct input to:
        fabric_cost_per_unit = (piece_weight_grams / 1000) × total_fabric_cost_per_kg_inr

    When dyeing is bypassed, dyeing_id is NULL and knitting_id provides the direct upstream link.
    """
    __tablename__ = "fabric_finishing"

    finishing_id                = Column(Integer, primary_key=True)
    dyeing_id                   = Column(Integer, nullable=True)    # FK → fabric_dyeing
    knitting_id                 = Column(Integer, nullable=True)    # FK → fabric_knitting (if dyeing bypassed)
    # finishing_operations: JSON array e.g. ["compacting", "stentering", "brushing"]
    finishing_operations_json   = Column(Text)
    compacting_shrinkage_pct    = Column(Numeric(5, 2))
    final_gsm_actual            = Column(Numeric(8, 2))
    finishing_unit_id           = Column(Integer, nullable=True)
    finishing_in_house          = Column(Boolean, default=False)
    quantity_kg                 = Column(Numeric(12, 4))
    cost_per_kg_inr             = Column(Numeric(10, 4))
    # MATERIALIZED COST CHAIN
    total_fabric_cost_per_kg_inr = Column(Numeric(10, 4))
    total_fabric_cost_per_kg_usd = Column(Numeric(10, 4))
    fx_rate_used                = Column(Numeric(8, 4))
    quality_result              = Column(String(50))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class GarmentConstruction(Base):
    """
    World 1 / Layer 5: The physical engineering specification of a garment.

    piece_weight_grams is the critical bridge between fabric cost/kg and garment cost/unit:
        fabric_cost_per_unit_inr = (piece_weight_fabric_only_grams / 1000)
                                    × total_fabric_cost_per_kg_inr

    complexity_score (1–10) drives CMT cost estimation via learned_coefficient:
        cmt_cost_per_dozen = cmt_minutes_per_dozen[complexity] × labour_rate_per_minute

    The special-technique flags (printing, embroidery, special_wash) add to complexity_score
    and each has its own cost line in the process steps.

    finishing_id is nullable — a garment_construction can be created speculatively (for quoting)
    before any actual fabric is produced.
    """
    __tablename__ = "garment_construction"

    construction_id             = Column(Integer, primary_key=True)
    finishing_id                = Column(Integer, nullable=True)    # FK → fabric_finishing
    silhouette                  = Column(String(50), nullable=False)
    # crew_neck_tee | v_neck_tee | polo | hoodie_pullover | hoodie_zip | sweatshirt
    # jogger | shorts | leggings | jacket_full_zip | jacket_half_zip | track_top | vest
    tech_pack_ref               = Column(String(100))
    # Construction metrics
    panel_count                 = Column(Integer)
    seam_count                  = Column(Integer)
    # stitch_types: JSON dict e.g. {"overlock_5t": true, "flat_seam": false, "chainstitch": true}
    stitch_types_json           = Column(Text)
    neck_construction           = Column(String(50))
    # binding | rib | self_fabric | collar
    pocket_count                = Column(Integer, default=0)
    has_zipper                  = Column(Boolean, default=False)
    has_drawstring              = Column(Boolean, default=False)
    has_ribbed_cuffs_hem        = Column(Boolean, default=False)
    # Special techniques
    has_printing                = Column(Boolean, default=False)
    print_type                  = Column(String(50))
    # screen_print | digital_print | sublimation | heat_transfer | pigment_print
    print_colour_count          = Column(Integer)
    print_placement             = Column(String(100))
    has_embroidery              = Column(Boolean, default=False)
    embroidery_stitch_count     = Column(Integer)
    embroidery_colour_count     = Column(Integer)
    has_special_wash            = Column(Boolean, default=False)
    wash_type                   = Column(String(50))
    # enzyme_wash | stone_wash | acid_wash | bleach_wash | silicone_wash
    # CRITICAL BRIDGE — piece weight
    piece_weight_grams          = Column(Numeric(8, 2))             # total garment incl. trims
    piece_weight_fabric_only_grams = Column(Numeric(8, 2))          # fabric component only (for cost calc)
    cutting_wastage_pct         = Column(Numeric(5, 2), default=10.0)
    measurement_surface_area_cm2 = Column(Numeric(10, 2))
    # Materialized when finishing_id is linked
    fabric_cost_per_unit_inr    = Column(Numeric(10, 4))
    # Complexity & CMT
    complexity_score            = Column(Integer)                   # 1-10
    target_cmt_minutes_per_dozen = Column(Numeric(8, 2))
    hs_code                     = Column(String(10))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)


class GarmentVariant(Base):
    """
    World 1 / Layer 6: The commercial and logistics identity of a specific garment colourway.

    One GarmentConstruction → many GarmentVariants (one per colour offered).
    carton dimensions drive container utilisation (cartons_per_40hq) which is an input
    to landed cost per unit.

    size_ratio_json: {"S":2,"M":4,"L":4,"XL":3} — the buyer's required assortment ratio,
    used to compute average weight per carton and average cost per carton.
    """
    __tablename__ = "garment_variant"

    variant_id                  = Column(Integer, primary_key=True)
    construction_id             = Column(Integer, nullable=False)   # FK → garment_construction
    style_number                = Column(String(100))               # universal buyer style number
    colour_name                 = Column(String(255))
    colour_code_buyer           = Column(String(100))
    colour_complexity_class     = Column(String(50))
    # standard | dark_premium | special_effect
    # size_range: JSON array e.g. ["XS","S","M","L","XL","XXL"]
    size_range_json             = Column(Text)
    # size_ratio: JSON dict e.g. {"S":2,"M":4,"L":4,"XL":3}
    size_ratio_json             = Column(Text)
    # Packing & logistics
    pack_method                 = Column(String(50))
    # flat_fold | hanger_pack | rolled | individual_polybag | master_carton_only
    fold_spec                   = Column(String(255))
    units_per_polybag           = Column(Integer, default=1)
    units_per_inner_carton      = Column(Integer, nullable=True)
    units_per_master_carton     = Column(Integer)
    carton_length_cm            = Column(Numeric(8, 2))
    carton_width_cm             = Column(Numeric(8, 2))
    carton_height_cm            = Column(Numeric(8, 2))
    carton_gross_weight_kg      = Column(Numeric(8, 2))
    cartons_per_40hq            = Column(Integer)
    cartons_per_20ft            = Column(Integer)
    retail_price_target_usd     = Column(Numeric(10, 4))
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(),
                                         onupdate=func.now(), nullable=False)
