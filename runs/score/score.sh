#!/bin/bash
#SBATCH --job-name=score
#SBATCH --output=/home/pacayang/Documents/Science/rapier/runs/score/logs/score.out
#SBATCH --error=/home/pacayang/Documents/Science/rapier/runs/score/logs/score.err
# ============================================================================
# Job Information
# ============================================================================
echo "Job ID: $SLURM_JOB_ID"
echo "Job Name: $SLURM_JOB_NAME"
echo "Node: $SLURM_NODELIST"
echo "Start Time: $(date)"
echo "Working Directory: $(pwd)"echo "============================================================================"

# ============================================================================
# Environment Setup
# ============================================================================
# Change to working directorycd /home/pacayang/Documents/Science/rapier/runs/score
# ============================================================================
# Pre-execution Setup
# ============================================================================
# ============================================================================
# Main Command Execution
# ============================================================================
echo ""
echo "Executing main command..."
echo "============================================================================"

# Set error handling
set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Exit on pipe failure

# Execute command
echo 'Executing score for task score'

EXIT_CODE=$?

# ============================================================================
# Post-execution Cleanup
# ============================================================================
# ============================================================================
# Job Summary
# ============================================================================
echo ""
echo "============================================================================"
echo "End Time: $(date)"
echo "Exit Code: $EXIT_CODE"echo "============================================================================"

exit $EXIT_CODE