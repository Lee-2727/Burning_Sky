Debug21 desktop test
1. Main desktop summary now explicitly shows color score.
2. 2D view shows phase, elapsed seconds, and side-sample batch progress. 0/6 remains until the single API batch returns; an active elapsed timer confirms UI activity.
3. Experimental tintable cloud fraction uses a channel-width gate: largest gap dominates and other gaps contribute at 25% weight, divided by max reach, multiplied by previous geometric illumination fraction. This is an uncalibrated 1D proxy, not true colored sky area.
4. Probability, peak color and original gap algorithm unchanged.
