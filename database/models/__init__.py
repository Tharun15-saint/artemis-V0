# ── Layer 0: Market signals ──────────────────────────────────────────────────
from .commodities import Cotton, CrudeOil, PxParaxylene, Pta, PolyesterPetChips, ViscoseRayon, CftcCottonCot
from .weather import CottonRegionWeather, IndiaHarvestSignal, CottonSupplyDemand
from .yarn_market import TirupurYarnMarketRate
from .market_data import (
    FxRates, CommodityFutures, Shipper, Consignee, TradeFlowSignals,
    FxInterestRates, FxVolatility, FxForwardCurve, FxCurrencyConfig,
)
from .logistics import BunkerFuelPrices, OceanFreightRates, RedSeaDisruption, LocalInlandFreight, AirFreight
from .costs import LabourCostByCountry, EnergyCost, FactoryFinancingCost, TrimCost, FobPriceCalculation
from .trade import (HsCodes, UsDutyRates, FreeTradeAgreements,
                    Uflpa, EuCsddd, DeMinimis,
                    UsDutyRateSchedule, UsDutyCountryEffectiveRate)
from .retail import (
    MajorRetailers,
    DemandSignals,
    SeasonalPatterns,
    RetailerFinancials,
    RetailerIntelligenceExtract,
    RetailerSignalEvidence,
    RetailerStockPrices,
)
from .reference_seed import (
    GeopoliticalRiskEvent,
    GovernmentExportIncentive,
    MarineInsurance,
    ShippingLaneRisk,
    UsImportDutyRate,
)

# ── Layer 1: Product chain (World 1) ─────────────────────────────────────────
from .yarn_fabric import Yarn, KnitFabric
from .product_chain import (
    FabricKnitting,
    FabricDyeing,
    FabricFinishing,
    GarmentConstruction,
    GarmentVariant,
)

# ── Layer 2: Actor network (World 2) ─────────────────────────────────────────
from .manufacturing import SpinningMills, KnittingMills, DyeingUnits, CmtFactories
from .network import Importer, Manufacturer, ManufacturerProfile, ImporterWorkingCapital, CompanyFactoryRelationship
from .actors import ActorRelationship, Person
from .company import (
    CompanyProfile, PurchaseOrderHistory,
    CostLayerPrior, CostVariablePrior,
    DiscoveredCostFactor, CostReasoningSession, CostOutcome,
)

# ── Layer 3: Commercial program (World 3) ────────────────────────────────────
from .program import ProductSpecification, Program

# ── Layer 4: Operations (World 4) ────────────────────────────────────────────
from .operations import Sample, PurchaseOrderLine, ProductionOrder, ProcessStep

# ── Layer 5: Financial records (World 5) ─────────────────────────────────────
from .financial import Invoice, ProgramPnl

# ── Layer 6: Communication corpus (World 6) ──────────────────────────────────
from .communications import CommunicationThread, EmailMessage

# ── Layer 7: Event registry (World 7) ────────────────────────────────────────
from .events import InternalEvent, ExternalEvent

# ── Layer 8: Market intelligence (World 8) ───────────────────────────────────
from .hedge import HedgeOpportunity, HedgePortfolio
from .scf import SupplyChainFinanceOffer
from .freight_execution import (Carrier, CarrierNetwork, OceanFreightRfq, CarrierBid,
                                 UsDrayageRfq, IntermodalRailRfq, OriginDrayageRfq)
from .customs import CustomsClearanceFiling, DutyDrawback
from .partners import PillarHqPartner, CustomsBrokerPartner, ScfProviderPartner

# ── Layer 9: Knowledge layer (World 9) ───────────────────────────────────────
from .knowledge import (
    LearnedCoefficient,
    ObservedPattern,
    DecisionRecord,
    KnowledgeGap,
    ReasoningChain,
)

# ── Platform outputs (derived/computed) ──────────────────────────────────────
from .outputs import (CurrentLandedCostPerDozen, ForwardLandedCost90Day,
                      MostCostEffectiveCorridor, CommodityRiskInOpenPrograms,
                      HedgeOpportunityRecommendation, Top5CompetitorSourcing,
                      RetailerDemandForecast, TariffExposureAnalysis,
                      FactoryFinancingImpact, FactoryCapacityConstraints,
                      OtdRiskScorePerProgram, FreightBookingWindow,
                      ScfOpportunityPerFactory, CompetitorFactoryIntel,
                      ProgramPnlWithLevers)
from .prediction import PredictionLog
from .revenue import RevenueTransaction, IntelligenceSubscription, DataLicensingRevenue
from .ingestion_log import IngestionLog

# ── Reconciled tables (previously DB-only, now first-class ORM models) ────────
from .reconciled_tables import (
    CottonPriceSeries,
    CottonPriceObservation,
    CrudeTransmissionCalibration,
    OceanFreightCorridorConfig,
    SignalCategoryTaxonomy,
    AffectedDecisionReference,
    QualityCheckLog,
    ImporterRetailerMix,
    RetailerSignalCorrelation,
)
