frappe.ui.form.on('Task', {
    refresh: function (frm) {
        if (frm.doc.percent_billed > 0) {
            frm.set_df_property('billable_amount', 'read_only', 1);
            frm.set_df_property('is_milestone', 'read_only', 1);
        }
        if (frm.doc.item) {
            frm.set_df_property('task_group', 'read_only', 1);
        }
    }
});