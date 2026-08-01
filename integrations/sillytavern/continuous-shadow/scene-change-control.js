/* CERA continuous-route shadow control. Not loaded by the active extension. */

const SCENE_CHANGE_CONTROL_ID = 'cera_scene_change_control';

export function installCeraSceneChangeControl(container, readCurrentControls) {
    if (!(container instanceof HTMLElement)) throw new TypeError('container is required');
    if (typeof readCurrentControls !== 'function') throw new TypeError('control reader is required');
    if (container.querySelector(`#${SCENE_CHANGE_CONTROL_ID}`)) return;

    const label = document.createElement('label');
    label.className = 'cera-control cera-scene-change-shadow';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = SCENE_CHANGE_CONTROL_ID;
    checkbox.checked = false;
    const text = document.createElement('span');
    text.textContent = 'Scene Change';
    label.append(checkbox, text);
    container.append(label);

    window.ceraContinuousShadowControls = () => ({
        ...readCurrentControls(),
        cera_scene_change: checkbox.checked === true,
    });
}

export function consumeCeraSceneChangeControl() {
    const checkbox = document.querySelector(`#${SCENE_CHANGE_CONTROL_ID}`);
    const enabled = checkbox?.checked === true;
    if (checkbox) checkbox.checked = false;
    return enabled;
}
