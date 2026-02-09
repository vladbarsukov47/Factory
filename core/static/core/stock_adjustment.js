(function () {
  function toggleFields() {
    const target = document.querySelector('#id_target_type');
    const mat = document.querySelector('#id_material');
    const prod = document.querySelector('#id_product');
    if (!target || !mat || !prod) return;

    const isMaterial = target.value === 'material';
    const isProduct  = target.value === 'product';

    mat.disabled  = !isMaterial;
    prod.disabled = !isProduct;

    if (!isMaterial) mat.value = '';
    if (!isProduct)  prod.value = '';
  }

  document.addEventListener('change', function (e) {
    if (e.target && e.target.id === 'id_target_type') toggleFields();
  });

  document.addEventListener('DOMContentLoaded', toggleFields);
})();
