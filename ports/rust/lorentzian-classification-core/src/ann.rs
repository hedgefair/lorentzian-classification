//! Approximate Nearest Neighbors classifier over the Lorentzian distance.
//!
//! Mirrors the `AnnState` lifecycle in the Python reference: features and labels
//! accumulate chronologically, and `run` performs the Pine approximate-kNN scan
//! with the `i % 4` chronological-spacing skip and the descending-distance gate.

use crate::types::{is_missing, Settings};

/// Holds the growing training set and the rolling neighbor buffers.
#[derive(Debug, Default)]
pub struct AnnState {
    features: [Vec<f64>; 5],
    labels: Vec<i64>,
    distances: Vec<f64>,
    predictions: Vec<i64>,
}

impl AnnState {
    /// Creates an empty state.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Appends one bar's feature vector and training label.
    pub fn push(&mut self, values: &[f64; 5], label: i64) {
        for (slot, &value) in self.features.iter_mut().zip(values.iter()) {
            slot.push(value);
        }
        self.labels.push(label);
    }

    /// Lorentzian distance between `values` and the stored bar at `idx`,
    /// summed over the first `feature_count` features. Returns `-inf` if any
    /// participating feature is missing (so the neighbor gate rejects it).
    fn distance(&self, idx: usize, values: &[f64; 5], feature_count: usize) -> f64 {
        let mut distance = 0.0;
        for (fidx, &current) in values.iter().enumerate().take(feature_count) {
            let historical = self.features[fidx][idx];
            if is_missing(current) || is_missing(historical) {
                return f64::NEG_INFINITY;
            }
            distance += (1.0 + (current - historical).abs()).ln();
        }
        distance
    }

    /// Runs the approximate-kNN scan and returns the summed neighbor prediction.
    pub fn run(&mut self, values: &[f64; 5], settings: &Settings, last_bar_index: i64) -> i64 {
        if self.labels.is_empty() {
            return 0;
        }
        let len = self.labels.len() as i64;
        let size_loop = (settings.max_bars_back - 1).min(len - 1);
        let max_bars_back_index = if last_bar_index >= settings.max_bars_back {
            last_bar_index - settings.max_bars_back
        } else {
            0
        };
        let start_idx = if settings.include_full_history {
            0
        } else {
            max_bars_back_index
        };

        let mut last_distance = -1.0_f64;
        let feature_count = settings.feature_count;

        // Ascending [start_idx, size_loop], or descending if start_idx > size_loop,
        // matching the Python iteration order exactly.
        let ascending = start_idx <= size_loop;
        let mut idx = start_idx;
        loop {
            if ascending {
                if idx > size_loop {
                    break;
                }
            } else if idx < size_loop {
                break;
            }

            let distance = self.distance(idx as usize, values, feature_count);
            if distance >= last_distance && idx % 4 != 0 {
                last_distance = distance;
                self.distances.push(distance);
                self.predictions.push(self.labels[idx as usize]);
                if self.predictions.len() as i64 > settings.neighbors_count {
                    let threshold_idx =
                        (settings.neighbors_count as f64 * 3.0 / 4.0).round_ties_even() as usize;
                    last_distance = self.distances[threshold_idx];
                    self.distances.remove(0);
                    self.predictions.remove(0);
                }
            }

            if ascending {
                idx += 1;
            } else {
                idx -= 1;
            }
        }

        self.predictions.iter().sum()
    }
}
