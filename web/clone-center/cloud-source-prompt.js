// Shared by the embedded clone page and the desktop cloud submit boundary.
export function cloudSourcePrompt(people, sourceDuration, outputDuration = 5) {
  let picture = 0;
  const definitions = people.map((person, index) => {
    const tags = (person.images || []).map(() => `<Picture ${++picture}>`);
    return `<Subject ${index + 1}> is the single target person defined by ${tags.join(', ')}. Keep this person's identity, face and clothing consistent throughout the video.`;
  });
  return `subject_definitions\n${definitions.join('\n')}\n<Video 1> is the trimmed source video. Use its visible action order, camera framing, timing and scene layout as the video reference. Replace source people with the target people defined by the pictures; do not copy the source people's identities or clothing.\n\nsummary\n[video editing] Render a full-color ${outputDuration}-second video with exactly ${people.length} target person(s). Retiming from the ${Number(sourceDuration).toFixed(2)}-second source clip should preserve its action order.\n\ndetailed_description\nPreserve coherent poses, contacts, occlusions and scene continuity. Keep each target identity stable across cuts. Do not add extra people or invented dialogue.\n\noverall_soundscape\nNatural ambient sound appropriate to the source scene.\n\nnon_diegetic_music\nN/A`;
}

// 纯文本模板；本地预览和云端提交共用，不调用视觉解析或翻译服务。
export function lightweightSourcePrompt(people, sourceDuration, outputDuration = sourceDuration, supplement = null) {
  let picture = 0;
  const retention = [];
  const definitions = people.map((person, index) => {
    const tags = (person.images || []).map(() => `<Picture ${++picture}>`);
    retention.push(`<Subject ${index + 1}>: attribute_transfer — use the assigned reference appearance throughout the source actions, with explicit user changes taking priority.`);
    return `<Subject ${index + 1}> is the target subject whose visible appearance comes from ${tags.join(', ')}. Bind this target to the source subject initially at screen ${person.position || 'center'}; follow the same subject through movement, occlusion and cuts. Use only visible reference features relevant to the user's edit.`;
  });
  definitions.push('<Video 1> is the source video to edit. It supplies action, timing, framing, camera movement, scene layout, physical contacts and visual style. Reference pictures supply target appearance only, never an opening or closing frame.');
  retention.push('<Video 1>: partially_preserved — replace the selected subjects; preserve unselected subjects, action, scene, shot order and camera movement unless the user requests changes.');
  const instruction = supplement?.text
    ? `\nUser instruction for ${supplement.target?.kind === 'subject' ? `<Subject ${supplement.target.subject_index}>` : 'the assigned target subjects and scene'} (apply only to this edit; explicitly requested changes override the corresponding source and reference constraints above): ${supplement.text}` : '';
  let openingPicture = 0;
  const openingBindings = people.map((person, index) => {
    const tags = (person.images || []).map(() => `<Picture ${++openingPicture}>`);
    return `<Subject ${index + 1}> replaces the source subject initially at screen ${person.position || 'center'}, using the appearance defined by ${tags.join(', ')}.`;
  }).join(' ');
  return `subject_definitions
${definitions.join('\n')}

summary
[video editing] The target video is an edited version of <Video 1>. Produce a ${Number(outputDuration).toFixed(3)}-second video. Keep the source visual style for unchanged content; replacement subjects use the rendering style explicitly requested by the user. Replace only the ${people.length} position-bound source subject(s) with the assigned targets. Explicit user instructions take priority over default preservation rules. ${Number(sourceDuration) === Number(outputDuration) ? 'Keep the original action timing.' : `Retime the ${Number(sourceDuration).toFixed(3)}-second source to the requested duration while preserving relative action and cut timing.`}

retention_analysis
${retention.join('\n')}

detailed_description
[Shot 1] Start with <Video 1>'s opening composition, framing, orientation and visible action. ${openingBindings} Apply the initial-position bindings once and maintain the same subject assignment thereafter. Render the target appearance from the pictures while following the source movement and contacts.
Continue through the source shots in their original order. Preserve every visible cut and its relative timing, shot size, camera angle, camera movement and speed. Keep the target appearance continuous across cuts.
Preserve only props actually visible in the source and the interactions with them. Match occlusions, illumination, contrast and perspective to the source scene.
Keep the source background and the style of unchanged content throughout, including the final frame. Do not cut to a reference picture or copy its background, pose or layout. End with the source final action performed by the replacement subjects. No invented accessories, subjects, dialogue or events. These are semantic editing instructions; no mask or frame-level structural control is supplied.${instruction}

overall_soundscape
Natural ambient and movement sounds appropriate to the source scene. No invented speech; no claim of copying the original audio track.

non_diegetic_music
N/A`;
}
