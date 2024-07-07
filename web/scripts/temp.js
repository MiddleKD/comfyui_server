// getNodeInputs.js 파일

export async function getNodeInputs(node) {
    const inputs = {};

    // Store all widget values
    const widgets = node.widgets;
    if (widgets) {
        for (const i in widgets) {
            const widget = widgets[i];
            if (!widget.options || widget.options.serialize !== false) {
                inputs[widget.name] = widget.serializeValue ? await widget.serializeValue(node, i) : widget.value;
            }
        }
    }

    // Store all node links
    for (let i in node.inputs) {
        let parent = node.getInputNode(i);
        if (parent) {
            let link = node.getInputLink(i);
            while (parent.mode === 4 || parent.isVirtualNode) {
                let found = false;
                if (parent.isVirtualNode) {
                    link = parent.getInputLink(link.origin_slot);
                    if (link) {
                        parent = parent.getInputNode(link.target_slot);
                        if (parent) {
                            found = true;
                        }
                    }
                } else if (link && parent.mode === 4) {
                    let allInputs = [link.origin_slot];
                    if (parent.inputs) {
                        allInputs = allInputs.concat(Object.keys(parent.inputs));
                        for (let parentInput of allInputs) {
                            if (parent.inputs[parentInput]?.type === node.inputs[i].type) {
                                link = parent.getInputLink(parentInput);
                                if (link) {
                                    parent = parent.getInputNode(parentInput);
                                }
                                found = true;
                                break;
                            }
                        }
                    }
                }

                if (!found) {
                    break;
                }
            }

            if (link) {
                if (parent?.updateLink) {
                    link = parent.updateLink(link);
                }
                if (link) {
                    inputs[node.inputs[i].name] = [String(link.origin_id), parseInt(link.origin_slot)];
                }
            }
        }
    }
    return Object.keys(inputs);
}
