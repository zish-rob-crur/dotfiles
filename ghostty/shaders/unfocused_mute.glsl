/*
  Unfocused mute shader for Ghostty.
  - Applies only when the surface is NOT focused (iFocus == 0).
  - Gently reduces saturation/contrast to make side panes less attention-grabbing.
  - Focused pane remains pixel-identical to the app output.
*/

vec3 desaturate(vec3 rgb, float amount) {
    float luma = dot(rgb, vec3(0.2126, 0.7152, 0.0722));
    return mix(rgb, vec3(luma), amount);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord.xy / iResolution.xy;
    vec4 col = texture(iChannel0, uv);

    // Only affect unfocused splits.
    if (iFocus <= 0) {
        vec3 bg = clamp(iBackgroundColor, 0.0, 1.0);

        float desatAmount = 0.22;
        float pullToBg = 0.07;
        float dim = 0.96;

        vec3 rgb = col.rgb;
        rgb = desaturate(rgb, desatAmount);
        rgb = mix(rgb, bg, pullToBg);
        rgb *= dim;

        col.rgb = clamp(rgb, 0.0, 1.0);
    }

    fragColor = col;
}
