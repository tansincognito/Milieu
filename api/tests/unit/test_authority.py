from app.pipeline.authority import assign_authority


def test_customer_statement_is_authority_4() -> None:
    assert (
        assign_authority(type_="requirement", actor_role="customer", stage="sales", speculative=False)
        == 4
    )


def test_product_decision_is_authority_4_regardless_of_speculative() -> None:
    assert (
        assign_authority(type_="decision", actor_role="product", stage="product", speculative=False)
        == 4
    )


def test_sales_restating_customer_is_authority_2() -> None:
    assert (
        assign_authority(type_="requirement", actor_role="sales", stage="sales", speculative=False)
        == 2
    )


def test_sales_commitment_is_authority_3() -> None:
    assert (
        assign_authority(type_="commitment", actor_role="sales", stage="sales", speculative=False)
        == 3
    )


def test_sales_hedged_suggestion_is_authority_1() -> None:
    assert (
        assign_authority(type_="requirement", actor_role="sales", stage="sales", speculative=True)
        == 1
    )


def test_engineering_decision_in_engineering_stage_is_authority_3() -> None:
    assert (
        assign_authority(
            type_="decision", actor_role="engineering", stage="engineering", speculative=False
        )
        == 3
    )


def test_unresolved_actor_role_with_no_stage_is_authority_0() -> None:
    assert assign_authority(type_="requirement", actor_role="other", stage=None, speculative=False) == 0
