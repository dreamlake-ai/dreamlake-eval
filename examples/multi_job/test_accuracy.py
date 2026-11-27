import pytest


@pytest.mark.parametrize("seed", [0, 1], ids=["seed-zero", "seed-one"])
def test_accuracy(seed, eval_trial):
    # Deliberately includes zero: a measured zero is not missing data.
    eval_trial.metric("reward", seed, step=0)
    eval_trial.metric("durationSeconds", 0.01, step=0)
    assert seed >= 0
