// 与本地克隆相同的六段默认编辑模板；无助手时保留原文和明确素材绑定。
export function materialReplacement(request) {
  let brief = request.user_prompt || request.prompt || '';
  if (/^subject_definitions\s*:/i.test(brief.trimStart())) return null;
  brief = brief.replace(/(?<!<)(?:视频|video)\s*(\d+)/gi,(_,n)=>`<Video ${Number(n)}>`)
    .replace(/(?<!<)(?:图片|图像|picture)\s*(\d+)/gi,(_,n)=>`<Picture ${Number(n)}>`);
  const pictures = new Set();
  let wholePerson = false;
  for (let clause of brief.split(/[。；;\n]/)) {
    clause = clause.replace(/(?:不要|不能|不是|do not|don't)\s*(?:只|仅|only).{0,12}(?:脸|face|facial)/gi,'');
    if (!/<Video\s+1>/i.test(clause) || /(?:不要|不必|无需|禁止|别|do not|don't)\s*(?:更换|替换|换|replace|change)/i.test(clause)
        || !/更换为|替换为|替换成|换成|换为|替换|replace|swap/i.test(clause)) continue;
    const tags = [...clause.matchAll(/<Picture\s+(\d+)>/gi)];
    if (!tags.length) continue;
    tags.forEach(match=>pictures.add(Number(match[1])));
    const person = /人物|角色|人(?=更换|替换|换成)|\b(?:person|people|woman|man|character)\b/i.test(clause);
    const attribute = /(?:人物|角色|人)[的\s]*(?:脸部|面部|脸|发型|头发|衣服|服装|衣着|背景)|\b(?:person|woman|man|character)(?:'s|’s)\s+(?:face|hair|clothes|clothing|outfit)|(?:只|仅)(?:改变|修改|更换|替换|换)?(?:脸|发型|衣服|服装|背景)/i.test(clause);
    wholePerson ||= person && !attribute;
  }
  if (!pictures.size) return null;
  const scope = brief.replace(/(?:不要|不能|不是|do not|don't)\s*(?:只|仅|only).{0,12}(?:脸|face|facial)/gi,'');
  const faceOnly = /只.{0,4}(?:换脸|脸|面部)|仅.{0,4}(?:脸|面部)|only.{0,15}(?:face|facial)/i.test(scope);
  return {pictures:[...pictures].sort((a,b)=>a-b).map(n=>`<Picture ${n}>`),wholePerson:wholePerson && !faceOnly};
}
export function materialTemplate(request, manifest) {
  const duration = Number(request.output_duration ?? request.end-request.start);
  const sourceDuration = Number(request.end-request.start);
  if (!Number.isFinite(duration) || duration < 4 || duration > 30 || !Number.isFinite(sourceDuration) || sourceDuration < 2 || sourceDuration > 15.001)
    throw new Error('请检查参考区间与云端生成时长');
  const pictures = manifest.filter(item=>item.kind==='Picture');
  const definitions = ['<Video 1> is the source video to edit, supplying the original shots, framing, actions and scene.'];
  const retention = ["<Video 1>: partially_preserved — apply all explicit user edits first; retain other subjects, background, subtitles, stickers, props, shot order and camera movement only where the request leaves them unchanged."];
  for (const item of pictures) {
    const subject = `<Subject ${item.number}>`;
    definitions.push(`${subject} is the reference appearance or visual attribute from ${item.tag} selected by the user. Apply it only to the source content named in the edit request; this definition does not add a cast member or imply that each picture depicts a different subject.`);
    retention.push(`${subject}: attribute_transfer — transfer only the requested reference features to the indicated source content; use the picture background or an opening/ending frame only if the user explicitly assigns that role.`);
  }
  const sections = {
    subject_definitions:definitions.join('\n'),
    summary:`[video editing] The target video is an edited version of <Video 1>. Produce a ${duration.toFixed(3)}-second video with only the user-requested changes. Explicit edits override only the corresponding source attributes.`,
    retention_analysis:retention.join('\n'),
    detailed_description:"[Shot 1] Apply the user edit request below as the authoritative target, including all exclusions and preservation requirements. Use the source opening composition and action only if the user does not request a different opening. Apply the reference features according to the user's bindings, retaining the source subject count and screen positions unless explicitly changed. A reference picture may supply multiple targets or an attribute rather than a new subject. Keep the edited appearance consistent through movement, interaction and occlusion.\n" +
      `Map the ${sourceDuration.toFixed(3)}-second source interval uniformly to ${duration.toFixed(3)} seconds, following the source shots in their original order and at their original relative cut times. Preserve each shot's camera angle, framing, camera movement or stationary camera, action timing, pose and physical contacts unless the request changes them. Do not add an unrequested camera move, shot, accessory, scene or event. Explicit camera, scene and action edits override these source defaults.\n` +
      "Change only the requested appearance or rendering style. Preserve the style of unchanged content. Keep original subtitles and visible text in their original language, wording, position, size, styling and timing, unless explicitly changed or removed; retain other unchanged stickers and overlays. Match scene lighting, perspective and contact shadows, integrating the edited subject naturally into the scene. End on the source final action and composition unless the user explicitly selects another ending or reference-picture keyframe.\nUser edit request (quoted verbatim; use its material tags to resolve all bindings): " + JSON.stringify(request.prompt),
    overall_soundscape:"Follow the source scene's ambience, movement sounds and dialogue timing unless the user requests audio changes. Do not invent dialogue or extra sound events.",
    non_diegetic_music:'Retain the source music if present and unchanged by the request; otherwise N/A. Do not add an unrelated score.',
  };
  // 完整替换人物时不要只换脸；保留用户明确指定的属性编辑范围。
  const contract = materialReplacement(request);
  if (contract?.wholePerson) sections.detailed_description += '\nReplacement binding: replace the source person with the complete visible appearance from ' + contract.pictures.join(', ') + ', including face, hair, build and clothing unless the user explicitly preserves an attribute. The source person supplies motion and placement only, not the target identity.';
  const prompt = Object.entries(sections).map(([key,value])=>`${key}\n${value}`).join('\n\n');
  if (Array.from(prompt).length>7000) throw new Error('默认模板超过 7000 字符，请缩短需求或减少素材；未截断提示词');
  return {prompt,manifest,plan:{schema:'clone.material-template/v1',duration},optimizer:{source:'template',name:'默认克隆模板'}};
}
